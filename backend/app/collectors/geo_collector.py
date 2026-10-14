"""Geography (D-10): one keyed Ariel search per direction per tick, folded to countries.

For each configured direction (SOURCE, DESTINATION) a run collects every settled bucket
since that direction's watermark with one keyed search over the whole estate
(`GEO::LOOKUP(<address>, 'country'), starttime / bucket`), splitting it in halves of whole
buckets while it is truncated. At one bucket a truncated result keeps its exact top and is
a LOWER_BOUND.

Guarantees:
  * Read-only: an aggregate search over ``events``. No address is selected, so none
    reaches this process. The lookup string is parsed in the pure fold (`app.geo.fold`)
    and never stored or logged.
  * No overlapping runs for one instance: the advisory lock is keyed on
    (instance, collector); a second worker skips.
  * A bucket that was not measured (FAILED, REJECTED, out of budget) is never written, so
    its absence is never a zero. Each direction's watermark holds at its last contiguous
    measured bucket, and a later run retries from there.
  * A measured bucket whose every address is unresolved stores only an UNRESOLVED row: the
    API reads that as INSUFFICIENT_DATA, never as "no foreign traffic".
  * A constant number of searches and statements per run, whatever the number of countries:
    one delete and one chunked bulk upsert for all directions (a chunk holds 3000 rows).
  * Idempotent: re-collecting a bucket replaces its rows, so a country that has left the
    top list leaves no stale row.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.bucketed_metrics import validate_bucketed_metric_window
from app.collectors.catchall_collector import floor_to_bucket
from app.core.config import Settings, get_settings
from app.geo.fold import (
    QUERY_VERSION,
    GeoDirection,
    GeoKeyedResult,
    GeoKeyedRow,
    fold_geo_bucket,
)
from app.ips.keyed_search import IpsSearchBudget
from app.models.enums import IpsCellState, SearchFamily
from app.models.geo import GeoCountryBucket
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from app.providers.base import (
    ProviderCapability,
    ProviderError,
    ProviderQueryRejectedError,
    QRadarProvider,
)
from app.services.locks import CollectorAdvisoryLock
from app.services.search_cadence import SearchCadenceService

logger = logging.getLogger("app.collectors.geo")

COLLECTOR_NAME = "geo_countries"
#: Rows per bulk statement: 10 columns, so 30000 bind parameters (asyncpg allows 32767).
_ROW_CHUNK = 3000
#: What a re-collected bucket row replaces; the key columns stay.
_UPDATED_COLUMNS = (
    "bucket_seconds",
    "country_code",
    "remainder_kind",
    "events",
    "measurement",
    "query_version",
    "collected_at",
)


def watermark_name(direction: GeoDirection) -> str:
    """The watermark of one (instance, direction): `CollectionWatermark.collector`."""
    return f"{COLLECTOR_NAME}_{direction.value.lower()}"


@dataclass(frozen=True)
class GeoBucketOutcome:
    """One bucket of one direction's search.

    `rows` holds every keyed row of the bucket: empty for a measured empty bucket
    (`COMPLETE`), the kept top for `LOWER_BOUND`, and always empty for the three states
    that carry no measurement.
    """

    bucket_start: datetime
    state: IpsCellState
    rows: tuple[GeoKeyedRow, ...] = ()

    def __post_init__(self) -> None:
        if self.rows and not self.measured:
            raise ValueError("a bucket without a measurement cannot hold rows")

    @property
    def measured(self) -> bool:
        return self.state in (IpsCellState.COMPLETE, IpsCellState.LOWER_BOUND)


async def run_geo_search(
    provider: QRadarProvider,
    direction: GeoDirection,
    window: tuple[datetime, datetime],
    bucket_seconds: int,
    *,
    max_rows: int,
    budget: IpsSearchBudget,
) -> tuple[list[GeoBucketOutcome], int]:
    """Every bucket of `window`, oldest first, and the number of splits.

    The split driver of the other keyed searches (`app.ips.keyed_search`): a whole search
    gives every bucket `COMPLETE`; a truncated one keeps nothing and is halved by time; at
    one bucket the exact top is kept as `LOWER_BOUND`. A failed search marks its buckets
    `FAILED` and is not split; a rejected one marks them `REJECTED` and its text is
    dropped; once the tick's budget is spent, every bucket not yet searched is
    `NOT_COLLECTED`, never zero.
    """
    start, end = validate_bucketed_metric_window(window[0], window[1], bucket_seconds)
    bucket_count = int((end - start).total_seconds()) // bucket_seconds
    buckets: dict[datetime, GeoBucketOutcome] = {}
    splits = 0

    def emit(
        first: int, count: int, state: IpsCellState, rows: tuple[GeoKeyedRow, ...] = ()
    ) -> None:
        by_bucket: dict[datetime, list[GeoKeyedRow]] = {}
        for row in rows:
            by_bucket.setdefault(row.bucket_start, []).append(row)
        for index in range(first, first + count):
            bucket_start = start + timedelta(seconds=bucket_seconds * index)
            buckets[bucket_start] = GeoBucketOutcome(
                bucket_start=bucket_start,
                state=state,
                rows=tuple(by_bucket.get(bucket_start, ())),
            )

    def contained(result: GeoKeyedResult, first: int, count: int) -> bool:
        """The provider's answer is about this search: nothing stray, nothing over the cap."""
        lo = start + timedelta(seconds=bucket_seconds * first)
        hi = lo + timedelta(seconds=bucket_seconds * count)
        return len(result.rows) <= max_rows and all(
            lo <= row.bucket_start < hi for row in result.rows
        )

    async def search(first: int, count: int) -> None:
        nonlocal splits
        if not budget.try_take():
            emit(first, count, IpsCellState.NOT_COLLECTED)
            return
        window_start = start + timedelta(seconds=bucket_seconds * first)
        try:
            result = await provider.get_geo_keyed(
                window_start,
                window_start + timedelta(seconds=bucket_seconds * count),
                direction=direction,
                bucket_seconds=bucket_seconds,
                max_rows=max_rows,
            )
        except ProviderQueryRejectedError as exc:
            # The text may quote the query or the appliance: class only.
            logger.warning(
                "geo %s search rejected (%s); %d buckets",
                direction.value,
                type(exc).__name__,
                count,
            )
            emit(first, count, IpsCellState.REJECTED)
            return
        except ProviderError as exc:
            logger.warning(
                "geo %s search failed (%s); %d buckets",
                direction.value,
                type(exc).__name__,
                count,
            )
            emit(first, count, IpsCellState.FAILED)
            return
        if not contained(result, first, count):
            logger.warning("geo %s search returned rows outside its scope", direction.value)
            emit(first, count, IpsCellState.FAILED)
            return
        if not result.truncated:
            emit(first, count, IpsCellState.COMPLETE, result.rows)
            return
        if count > 1:
            splits += 1
            left = count // 2
            await search(first, left)
            await search(first + left, count - left)
        else:
            emit(first, count, IpsCellState.LOWER_BOUND, result.rows)

    await search(0, bucket_count)
    return [buckets[key] for key in sorted(buckets)], splits


@dataclass
class GeoCollectionReport:
    instance_id: uuid.UUID
    directions: list[str]
    buckets_attempted: int = 0
    buckets_complete: int = 0
    buckets_lower_bound: int = 0
    #: FAILED or REJECTED: searched, no measurement.
    buckets_failed: int = 0
    #: The tick's search budget ran out before the bucket was searched.
    buckets_not_collected: int = 0
    #: Ariel searches sent, failed ones included.
    searches_sent: int = 0
    #: Times a search was divided in halves because it was truncated.
    splits: int = 0
    rows_written: int = 0
    #: Keys that were neither a country nor ``{}``, counted in distinct keys per bucket.
    unparseable_keys: int = 0
    skipped_locked: bool = False
    unsupported: bool = False
    partial_failure: bool = False
    error: str | None = None
    duration_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "status": (
                "skipped_locked"
                if self.skipped_locked
                else "unsupported"
                if self.unsupported
                else "failed"
                if self.partial_failure
                else "ok"
            ),
            "directions": self.directions,
            "buckets_attempted": self.buckets_attempted,
            "buckets_complete": self.buckets_complete,
            "buckets_lower_bound": self.buckets_lower_bound,
            "buckets_failed": self.buckets_failed,
            "buckets_not_collected": self.buckets_not_collected,
            "searches_sent": self.searches_sent,
            "splits": self.splits,
            "rows_written": self.rows_written,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


class GeoCollector:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def collect(self, instance: QRadarInstance) -> GeoCollectionReport:
        started = self._clock()
        settings = self.settings
        directions = [GeoDirection(item) for item in settings.geo_directions]
        report = GeoCollectionReport(
            instance_id=instance.id, directions=[d.value for d in directions]
        )

        if not self.provider.supports(ProviderCapability.AQL_EXECUTION):
            # Nothing was attempted, so nothing is recorded.
            report.unsupported = True
            report.error = "provider cannot run Ariel searches"
            return report

        async with CollectorAdvisoryLock(
            self.session, settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                report.skipped_locked = True
                return report

            bucket_seconds = settings.collection_interval_seconds
            cadence = await SearchCadenceService(self.session, settings, self._clock).get(
                SearchFamily.GEO_COLLECTION
            )
            cadence_buckets = max(1, cadence.cadence_seconds // bucket_seconds)
            boundary = floor_to_bucket(
                started - timedelta(seconds=settings.search_cadence_settle_seconds),
                bucket_seconds,
            )
            watermarks = await self._get_or_create_watermarks(instance, directions)
            budget = IpsSearchBudget(settings.search_cadence_max_searches_per_tick)
            rows: list[dict] = []
            replaced: dict[GeoDirection, list[datetime]] = {}
            for direction in directions:
                watermark = watermarks[direction]
                starts = self._buckets_to_collect(
                    watermark,
                    boundary,
                    bucket_seconds,
                    first_run_buckets=cadence_buckets,
                    max_buckets=cadence_buckets + settings.collection_max_backfill_intervals,
                )
                outcomes: list[GeoBucketOutcome] = []
                if starts:
                    outcomes, splits = await run_geo_search(
                        self.provider,
                        direction,
                        (starts[0], starts[-1] + timedelta(seconds=bucket_seconds)),
                        bucket_seconds,
                        max_rows=settings.geo_max_rows_per_search,
                        budget=budget,
                    )
                    report.splits += splits
                for outcome in outcomes:
                    self._count(outcome, report)
                    if not outcome.measured:
                        continue
                    replaced.setdefault(direction, []).append(outcome.bucket_start)
                    rows.extend(
                        self._bucket_rows(
                            instance, direction, outcome, bucket_seconds, started, report
                        )
                    )
                self._advance_watermark(watermark, outcomes, direction, report)
                watermark.last_run_at = started
                if watermark.watermark_at is not None:
                    end_of_last = watermark.watermark_at + timedelta(seconds=bucket_seconds)
                    watermark.lag_seconds = max(0, int((started - end_of_last).total_seconds()))
                watermark.collection_metadata = {
                    "bucket_seconds": bucket_seconds,
                    "direction": direction.value,
                }
            report.searches_sent = budget.used
            await self._write(instance, replaced, rows, report)
            await self.session.flush()

        report.duration_ms = int((self._clock() - started).total_seconds() * 1000)
        logger.info(
            "geo collection complete",
            extra={
                "instance_id": str(instance.id),
                "buckets_attempted": report.buckets_attempted,
                "buckets_complete": report.buckets_complete,
                "buckets_lower_bound": report.buckets_lower_bound,
                "buckets_failed": report.buckets_failed,
                "buckets_not_collected": report.buckets_not_collected,
                "rows_written": report.rows_written,
                "unparseable_keys": report.unparseable_keys,
                "duration_ms": report.duration_ms,
            },
        )
        return report

    # ------------------------------------------------------------------ window
    @staticmethod
    def _buckets_to_collect(
        watermark: CollectionWatermark,
        boundary: datetime,
        bucket_seconds: int,
        *,
        first_run_buckets: int,
        max_buckets: int,
    ) -> list[datetime]:
        """Settled bucket starts after the watermark, oldest first, at most `max_buckets`."""
        step = timedelta(seconds=bucket_seconds)
        if watermark.watermark_at is None:
            # First run: one cadence of buckets, never a backfill nobody asked for.
            first = boundary - step * first_run_buckets
            limit = first_run_buckets
        else:
            last = watermark.watermark_at
            first = (last if last.tzinfo else last.replace(tzinfo=UTC)) + step
            limit = max_buckets
        out: list[datetime] = []
        cursor = first
        while cursor + step <= boundary and len(out) < limit:
            out.append(cursor)
            cursor += step
        return out

    # -------------------------------------------------------------------- fold
    @staticmethod
    def _count(outcome: GeoBucketOutcome, report: GeoCollectionReport) -> None:
        report.buckets_attempted += 1
        if outcome.state is IpsCellState.COMPLETE:
            report.buckets_complete += 1
        elif outcome.state is IpsCellState.LOWER_BOUND:
            report.buckets_lower_bound += 1
        elif outcome.state is IpsCellState.NOT_COLLECTED:
            report.buckets_not_collected += 1
        else:
            report.buckets_failed += 1

    def _bucket_rows(
        self,
        instance: QRadarInstance,
        direction: GeoDirection,
        outcome: GeoBucketOutcome,
        bucket_seconds: int,
        now: datetime,
        report: GeoCollectionReport,
    ) -> list[dict]:
        """The stored rows of one measured bucket: countries, then the remainders."""
        fact = fold_geo_bucket(
            outcome.rows, max_countries=self.settings.geo_max_countries_per_bucket
        )
        report.unparseable_keys += fact.unparseable_keys
        measurement = "LOWER_BOUND" if outcome.state is IpsCellState.LOWER_BOUND else "EXACT"

        def make(code: str | None, remainder: str | None, events: int) -> dict:
            return {
                "instance_id": instance.id,
                "bucket_start": outcome.bucket_start,
                "bucket_seconds": bucket_seconds,
                "direction": direction.value,
                "country_code": code,
                "remainder_kind": remainder,
                "events": events,
                "measurement": measurement,
                "query_version": QUERY_VERSION,
                "collected_at": now,
            }

        out = [make(code, None, events) for code, events in fact.countries]
        for kind, events in (
            ("UNRESOLVED", fact.unresolved),
            ("OVER_CAP", fact.over_cap),
            ("UNPARSEABLE", fact.unparseable),
        ):
            # A remainder with no events is not a row, except UNRESOLVED when the bucket
            # would otherwise have none: the row is what says the bucket was measured.
            if events > 0 or (kind == "UNRESOLVED" and not out):
                out.append(make(None, kind, events))
        return out

    # ------------------------------------------------------------------ writes
    async def _write(
        self,
        instance: QRadarInstance,
        replaced: dict[GeoDirection, list[datetime]],
        rows: list[dict],
        report: GeoCollectionReport,
    ) -> None:
        """Replace the measured buckets: one delete, then a chunked bulk upsert."""
        if not rows:
            return
        conditions = [
            (GeoCountryBucket.direction == direction.value)
            & GeoCountryBucket.bucket_start.in_(sorted(starts))
            for direction, starts in sorted(replaced.items(), key=lambda item: item[0].value)
            if starts
        ]
        if conditions:
            await self.session.execute(
                delete(GeoCountryBucket).where(
                    GeoCountryBucket.instance_id == instance.id, or_(*conditions)
                )
            )
        for offset in range(0, len(rows), _ROW_CHUNK):
            chunk = rows[offset : offset + _ROW_CHUNK]
            stmt = pg_insert(GeoCountryBucket).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["instance_id", "bucket_start", "direction", "slot"],
                set_={column: stmt.excluded[column] for column in _UPDATED_COLUMNS},
            )
            await self.session.execute(stmt)
        report.rows_written = len(rows)

    @staticmethod
    def _advance_watermark(
        watermark: CollectionWatermark,
        outcomes: list[GeoBucketOutcome],
        direction: GeoDirection,
        report: GeoCollectionReport,
    ) -> None:
        """Hold the watermark at the last contiguous measured bucket."""
        advanced = 0
        for outcome in outcomes:
            if not outcome.measured:
                break
            watermark.watermark_at = outcome.bucket_start
            advanced += 1
        watermark.intervals_collected += advanced
        unmeasured = [o for o in outcomes if not o.measured]
        if unmeasured:
            report.partial_failure = True
            states = sorted({o.state.value for o in unmeasured})
            error = f"{len(unmeasured)} bucket(s) not measured: {', '.join(states)}"
            report.error = f"{report.error}; {direction.value}: {error}" if report.error else (
                f"{direction.value}: {error}"
            )
            watermark.consecutive_failures += 1
            watermark.last_error = error
        else:
            watermark.consecutive_failures = 0
            watermark.last_error = None

    # ------------------------------------------------------------------- loads
    async def _get_or_create_watermarks(
        self, instance: QRadarInstance, directions: list[GeoDirection]
    ) -> dict[GeoDirection, CollectionWatermark]:
        """One watermark per direction, loaded in one statement."""
        names = {watermark_name(direction): direction for direction in directions}
        found = await self.session.scalars(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance.id,
                CollectionWatermark.collector.in_(sorted(names)),
            )
        )
        out: dict[GeoDirection, CollectionWatermark] = {
            names[w.collector]: w for w in found.all()
        }
        created = False
        for direction in directions:
            if direction not in out:
                watermark = CollectionWatermark(
                    instance_id=instance.id, collector=watermark_name(direction)
                )
                self.session.add(watermark)
                out[direction] = watermark
                created = True
        if created:
            await self.session.flush()
        return out
