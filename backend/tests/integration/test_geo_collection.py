"""Geo collection against a real database (D-10, G-2)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from app.collectors.geo_collector import GeoCollector, watermark_name
from app.core.config import get_settings
from app.geo.fold import GeoDirection
from app.models.enums import SearchFamily
from app.models.geo import GeoCountryBucket
from app.models.monitoring import CollectionWatermark
from app.models.search_cadence import SearchCadenceSetting
from app.providers.base import ProviderError
from app.providers.mock import MockQRadarProvider, geo_key
from app.providers.qradar_mcp import QRadarMCPProvider
from app.services.geo_retention import GeoRetention
from tests.integration.factories import make_instance
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = pytest.mark.integration

BUCKET = 300
NOW = datetime(2026, 10, 13, 12, 10, tzinfo=UTC)


@pytest_asyncio.fixture
async def pin_cadence(db_session):  # type: ignore[no-untyped-def]
    """The pre-D-28 one-hour cadence (12 buckets per tick) these scenarios were written for."""
    db_session.add(
        SearchCadenceSetting(
            family=SearchFamily.GEO_COLLECTION.value, cadence_seconds=3600, updated_at=NOW
        )
    )
    await db_session.commit()
#: With the default 120 s settle, the run at NOW closes at 12:05, so its first-run
#: window is the twelve buckets 11:05 .. 12:00.
FIRST = datetime(2026, 10, 13, 11, 5, tzinfo=UTC)
LAST = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
SOURCE, DESTINATION = GeoDirection.SOURCE, GeoDirection.DESTINATION


def at(index: int) -> datetime:
    return FIRST + timedelta(seconds=BUCKET * index)


def country(code: str, events: int = 10) -> dict:
    return {"geo": geo_key(code), "events": events}


def unresolved(events: int = 10) -> dict:
    return {"geo": "{}", "events": events}


class Script(MockQRadarProvider):
    """A mock whose lookups are `script(direction, bucket_start)`."""

    def __init__(self, script) -> None:  # type: ignore[no-untyped-def]
        super().__init__(seed=1, now=NOW)
        self.geo_script = lambda direction, key: script(
            direction, datetime.fromtimestamp(key * BUCKET, tz=UTC)
        )


def steady(rows):  # type: ignore[no-untyped-def]
    return lambda direction, start: rows


def collector(session, provider, *, now=NOW, **overrides) -> GeoCollector:  # type: ignore[no-untyped-def]
    settings = get_settings().model_copy(
        update={"search_cadence_max_searches_per_tick": 50, **overrides}
    )
    return GeoCollector(session, provider, settings=settings, clock=lambda: now)


async def stored(session, **where) -> list[GeoCountryBucket]:  # type: ignore[no-untyped-def]
    query = select(GeoCountryBucket).execution_options(populate_existing=True)
    for column, value in where.items():
        query = query.where(getattr(GeoCountryBucket, column) == value)
    rows = await session.scalars(
        query.order_by(
            GeoCountryBucket.direction, GeoCountryBucket.bucket_start, GeoCountryBucket.slot
        )
    )
    return list(rows.all())


async def watermark(session, inst, direction) -> CollectionWatermark:  # type: ignore[no-untyped-def]
    return await session.scalar(
        select(CollectionWatermark)
        .where(
            CollectionWatermark.instance_id == inst.id,
            CollectionWatermark.collector == watermark_name(direction),
        )
        .execution_options(populate_existing=True)
    )


@pytest.mark.usefixtures("pin_cadence")
class TestRows:
    async def test_countries_and_the_unresolved_remainder_are_stored_per_bucket(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        provider = Script(steady([country("US", 40), country("DE", 5), unresolved(7)]))

        report = await collector(db_session, provider).collect(inst)

        assert report.buckets_complete == 24 and report.searches_sent == 2
        assert report.rows_written == 24 * 3
        first = await stored(db_session, direction="SOURCE", bucket_start=FIRST)
        assert [(r.slot, r.country_code, r.remainder_kind, r.events) for r in first] == [
            ("DE", "DE", None, 5),
            ("UNRESOLVED", None, "UNRESOLVED", 7),
            ("US", "US", None, 40),
        ]
        assert {r.measurement for r in first} == {"EXACT"}
        assert first[0].bucket_seconds == BUCKET and first[0].collected_at == NOW
        assert {r.query_version for r in first} == {"geo-country-v1"}

    async def test_an_all_unresolved_bucket_stores_only_an_unresolved_row(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)

        await collector(db_session, Script(steady([unresolved(30)]))).collect(inst)

        rows = await stored(db_session, direction="SOURCE")
        assert len(rows) == 12
        assert {(r.slot, r.country_code, r.events) for r in rows} == {("UNRESOLVED", None, 30)}

    async def test_a_measured_empty_bucket_is_an_unresolved_zero_never_no_row(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)

        await collector(db_session, Script(steady([]))).collect(inst)

        rows = await stored(db_session, direction="SOURCE")
        assert len(rows) == 12 and {(r.slot, r.events) for r in rows} == {("UNRESOLVED", 0)}

    async def test_two_strings_for_one_code_are_summed(self, db_session) -> None:
        inst = await make_instance(db_session)
        rows = [
            {"geo": geo_key("US", confidence=None), "events": 4},
            {"geo": geo_key("US", confidence=90), "events": 6},
        ]

        await collector(db_session, Script(steady(rows))).collect(inst)

        us = await stored(db_session, direction="SOURCE", slot="US")
        assert len(us) == 12 and {r.events for r in us} == {10}

    async def test_the_cap_sums_the_rest_into_over_cap_and_the_events_add_up(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        codes = [f"{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(70)]

        await collector(
            db_session, Script(steady([country(c, 3) for c in codes])), geo_directions=["SOURCE"]
        ).collect(inst)

        bucket = await stored(db_session, bucket_start=FIRST)
        assert len([r for r in bucket if r.country_code]) == 64
        over = [r for r in bucket if r.remainder_kind == "OVER_CAP"]
        assert len(over) == 1 and over[0].events == 6 * 3
        assert sum(r.events for r in bucket) == 70 * 3

    async def test_an_unparseable_key_is_counted_never_stored_or_logged(
        self, db_session, caplog: pytest.LogCaptureFixture
    ) -> None:
        inst = await make_instance(db_session)
        hostile = '{"country":{"iso_code":"SECRET-CODE"},"ip":"203.0.113.9"}'
        rows = [country("US", 4), {"geo": hostile, "events": 9}]

        with caplog.at_level(logging.DEBUG):
            report = await collector(
                db_session, Script(steady(rows)), geo_directions=["SOURCE"]
            ).collect(inst)

        bad = await stored(db_session, slot="UNPARSEABLE")
        assert len(bad) == 12 and {r.events for r in bad} == {9}
        assert report.unparseable_keys == 12
        dump = await db_session.execute(text("SELECT g::text FROM geo_country_bucket g"))
        everything = " ".join(row[0] for row in dump)
        assert "SECRET" not in everything and "203.0.113.9" not in everything
        assert "SECRET" not in caplog.text and "203.0.113.9" not in caplog.text

    async def test_recollecting_a_bucket_replaces_it_and_leaves_no_stale_country(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await collector(
            db_session, Script(steady([country("US", 5), country("FR", 5)])),
            geo_directions=["SOURCE"],
        ).collect(inst)
        wm = await watermark(db_session, inst, SOURCE)
        wm.watermark_at = FIRST - timedelta(seconds=BUCKET)
        await db_session.flush()

        await collector(
            db_session, Script(steady([country("US", 9)])), geo_directions=["SOURCE"]
        ).collect(inst)

        rows = await stored(db_session, direction="SOURCE")
        assert {(r.slot, r.events) for r in rows} == {("US", 9)} and len(rows) == 12


@pytest.mark.usefixtures("pin_cadence")
class TestDirections:
    async def test_both_directions_run_with_separate_watermarks_and_series(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)

        def script(direction, start):  # type: ignore[no-untyped-def]
            return [country("US" if direction is SOURCE else "DE", 11)]

        provider = Script(script)
        report = await collector(db_session, provider).collect(inst)

        assert [s.direction for s in provider.geo_searches] == [SOURCE, DESTINATION]
        assert report.searches_sent == 2
        assert {r.slot for r in await stored(db_session, direction="SOURCE")} == {"US"}
        assert {r.slot for r in await stored(db_session, direction="DESTINATION")} == {"DE"}
        for direction in (SOURCE, DESTINATION):
            wm = await watermark(db_session, inst, direction)
            assert wm.watermark_at == LAST and wm.intervals_collected == 12
            assert wm.collection_metadata["direction"] == direction.value
        assert watermark_name(SOURCE) != watermark_name(DESTINATION)

    async def test_one_direction_failing_does_not_move_the_other_watermark_back(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        provider = Script(steady([country("US")]))
        provider.geo_fault = lambda s: (
            ProviderError("down") if s.direction is DESTINATION else None
        )

        report = await collector(db_session, provider).collect(inst)

        assert report.partial_failure and report.buckets_failed == 12
        assert (await watermark(db_session, inst, SOURCE)).watermark_at == LAST
        dest = await watermark(db_session, inst, DESTINATION)
        assert dest.watermark_at is None and dest.consecutive_failures == 1
        assert await stored(db_session, direction="DESTINATION") == []
        assert len(await stored(db_session, direction="SOURCE")) == 12

    async def test_the_setting_chooses_the_directions(self, db_session) -> None:
        inst = await make_instance(db_session)
        provider = Script(steady([country("US")]))

        await collector(db_session, provider, geo_directions=["DESTINATION"]).collect(inst)

        assert [s.direction for s in provider.geo_searches] == [DESTINATION]
        assert await stored(db_session, direction="SOURCE") == []


SOURCE_ONLY = {"geo_directions": ["SOURCE"]}


@pytest.mark.usefixtures("pin_cadence")
class TestSplitAndFailure:
    async def test_a_truncated_wide_search_is_split_by_time_and_nothing_wide_is_kept(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        provider = Script(steady([country("US", 10)]))
        provider.geo_force_truncated = lambda s: s.window_end - s.window_start > timedelta(
            seconds=BUCKET
        )

        report = await collector(db_session, provider, **SOURCE_ONLY).collect(inst)

        assert report.buckets_complete == 12 and report.splits > 0
        assert report.searches_sent == len(provider.geo_searches) > 1
        # Halves first: the second search is the left half of the window.
        assert provider.geo_searches[1].window_end - provider.geo_searches[1].window_start == (
            timedelta(seconds=BUCKET * 6)
        )
        rows = await stored(db_session)
        assert len(rows) == 12 and {r.measurement for r in rows} == {"EXACT"}

    async def test_one_bucket_truncated_is_a_lower_bound_with_its_top_kept(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        provider = Script(steady([country("US", 10)]))
        provider.geo_force_truncated = lambda s: True

        report = await collector(db_session, provider, **SOURCE_ONLY).collect(inst)

        assert report.buckets_lower_bound == 12 and report.buckets_complete == 0
        rows = await stored(db_session)
        assert {r.measurement for r in rows} == {"LOWER_BOUND"} and len(rows) == 12
        assert (await watermark(db_session, inst, SOURCE)).watermark_at == LAST

    async def test_a_failed_bucket_has_no_row_and_the_watermark_holds_before_it(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        provider = Script(steady([country("US", 10)]))
        bad = at(4)
        provider.geo_force_truncated = lambda s: s.window_end - s.window_start > timedelta(
            seconds=BUCKET
        )
        provider.geo_fault = lambda s: (
            ProviderError("down")
            if s.window_start == bad and s.window_end - s.window_start == timedelta(seconds=BUCKET)
            else None
        )

        report = await collector(db_session, provider, **SOURCE_ONLY).collect(inst)

        assert report.buckets_failed == 1 and report.partial_failure
        starts = {r.bucket_start for r in await stored(db_session)}
        assert bad not in starts and len(starts) == 11
        wm = await watermark(db_session, inst, SOURCE)
        assert wm.watermark_at == at(3) and wm.consecutive_failures == 1
        assert wm.last_error and "FAILED" in wm.last_error
        # The next run retries from the failed bucket and fills it.
        again = await collector(db_session, provider, now=NOW, **SOURCE_ONLY).collect(inst)
        assert again.buckets_failed == 1
        provider.geo_fault = None
        await collector(db_session, provider, now=NOW, **SOURCE_ONLY).collect(inst)
        assert len({r.bucket_start for r in await stored(db_session)}) == 12
        assert (await watermark(db_session, inst, SOURCE)).watermark_at == LAST

    async def test_a_rejected_search_has_no_row(self, db_session) -> None:
        from app.providers.base import ProviderQueryRejectedError

        inst = await make_instance(db_session)
        provider = Script(steady([country("US")]))
        provider.geo_fault = lambda s: ProviderQueryRejectedError("SECRET parse error")

        report = await collector(db_session, provider, **SOURCE_ONLY).collect(inst)

        assert report.buckets_failed == 12 and await stored(db_session) == []
        assert "SECRET" not in (report.error or "")

    async def test_the_per_tick_cap_leaves_later_buckets_unsearched_and_the_watermark_held(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        provider = Script(steady([country("US", 10)]))
        provider.geo_force_truncated = lambda s: s.window_end - s.window_start > timedelta(
            seconds=BUCKET
        )

        report = await collector(
            db_session, provider, search_cadence_max_searches_per_tick=3, **SOURCE_ONLY
        ).collect(inst)

        assert report.searches_sent == 3 == len(provider.geo_searches)
        assert report.buckets_not_collected > 0 and report.buckets_complete == 0
        assert await stored(db_session) == []
        wm = await watermark(db_session, inst, SOURCE)
        assert wm.watermark_at is None and wm.intervals_collected == 0

    async def test_the_cap_keeps_the_measured_prefix(self, db_session) -> None:
        inst = await make_instance(db_session)
        provider = Script(steady([country("US", 10)]))
        provider.geo_force_truncated = lambda s: s.window_end - s.window_start > timedelta(
            seconds=BUCKET * 3
        )

        report = await collector(
            db_session, provider, search_cadence_max_searches_per_tick=4, **SOURCE_ONLY
        ).collect(inst)

        measured = {r.bucket_start for r in await stored(db_session)}
        assert report.buckets_not_collected > 0 and measured
        wm = await watermark(db_session, inst, SOURCE)
        assert wm.watermark_at == max(m for m in measured if all(
            at(i) in measured for i in range(int((m - FIRST) / timedelta(seconds=BUCKET)) + 1)
        ))

    async def test_a_provider_without_aql_records_nothing(self, db_session) -> None:
        inst = await make_instance(db_session)
        provider = QRadarMCPProvider(base_url="http://mcp.invalid:1234")

        report = await collector(db_session, provider).collect(inst)

        assert report.unsupported and await stored(db_session) == []
        count = await db_session.scalar(select(func.count()).select_from(CollectionWatermark))
        assert count == 0


@pytest.mark.usefixtures("pin_cadence")
class TestConstantCost:
    async def run_with(self, session, n_countries: int) -> tuple[int, int]:  # type: ignore[no-untyped-def]
        inst = await make_instance(session)
        codes = [f"{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(n_countries)]
        provider = Script(steady([country(c, 5) for c in codes] + [unresolved(3)]))
        with recorded_statements(session) as statements:
            report = await collector(session, provider).collect(inst)
        assert report.searches_sent == 2
        return len(statements), report.rows_written

    async def test_the_statement_count_is_the_same_at_2_and_at_64_countries(
        self, db_session
    ) -> None:
        small, small_rows = await self.run_with(db_session, 2)
        large, large_rows = await self.run_with(db_session, 64)
        capped, capped_rows = await self.run_with(db_session, 200)

        assert small_rows == 24 * 3 and large_rows == 24 * 65 and capped_rows == 24 * 66
        assert small == large == capped

    async def test_the_search_count_is_the_same_whatever_the_countries(self, db_session) -> None:
        for n in (2, 64):
            inst = await make_instance(db_session)
            codes = [f"{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(n)]
            provider = Script(steady([country(c, 5) for c in codes]))
            await collector(db_session, provider).collect(inst)
            assert len(provider.geo_searches) == 2


@pytest.mark.usefixtures("pin_cadence")
class TestRetention:
    async def test_old_buckets_are_deleted_in_batches_and_recent_ones_kept(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await collector(db_session, Script(steady([country("US")]))).collect(inst)
        old = NOW - timedelta(days=40)
        for direction in ("SOURCE", "DESTINATION"):
            db_session.add(
                GeoCountryBucket(
                    instance_id=inst.id, bucket_start=old, direction=direction,
                    bucket_seconds=BUCKET, country_code="US", events=1, measurement="EXACT",
                    query_version="geo-country-v1", collected_at=old,
                )
            )
        await db_session.flush()
        settings = get_settings().model_copy(update={"geo_retention_batch_size": 100})

        report = await GeoRetention(
            db_session, settings=settings, clock=lambda: NOW
        ).run()

        assert report.deleted == {"geo_country_bucket": 2} and report.incomplete == []
        assert len(await stored(db_session)) == 24
