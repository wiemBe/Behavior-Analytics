"""Phase D IPS fleet collection, admission, honesty states and SQL bounds."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, insert, select, text

from app.collectors.ips_collector import IpsCollector, watermark_collector
from app.core.config import Settings, get_settings
from app.ips.profile import IPS_PRESETS
from app.models.enums import (
    IpsCellState,
    IpsDimension,
    IpsPreset,
    IpsProfileValidationStatus,
)
from app.models.ips import IpsEntity, IpsEntityMetric, IpsSourceBucket, IpsSourceProfile
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderError, ProviderQueryRejectedError
from app.providers.mock import MockQRadarProvider
from app.services.ips_pipeline import ips_pipeline_lock_key
from app.services.locks import collector_lock_key
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 13, 10, 7, tzinfo=UTC)
BUCKET = timedelta(minutes=5)


def cfg(**overrides: object) -> Settings:
    values = get_settings().model_dump()
    values.update(
        ips_collection_enabled=True,
        collection_interval_seconds=300,
        collection_max_backfill_intervals=0,
        search_cadence_max_searches_per_tick=64,
        ariel_multi_bucket_max_rows=5000,
        ips_max_entities_per_bucket=200,
        ips_max_new_entities_per_bucket=20,
        ips_max_active_entities_per_source_dimension=2000,
        ips_min_admission_events=3,
    )
    values.update(overrides)
    return Settings(**values)


async def add_source(  # type: ignore[no-untyped-def]
    session, instance, qradar_id: int, *, valid: bool = True, **source_kw
):
    source = await make_log_source(
        session, instance, qradar_id=qradar_id, name=f"ips-{qradar_id}", **source_kw
    )
    mapping = IPS_PRESETS[IpsPreset.CISCO_FTD]
    profile = IpsSourceProfile(
        instance_id=instance.id,
        log_source_id=source.id,
        enabled=True,
        profile_version=1,
        preset=IpsPreset.CISCO_FTD,
        mapping=mapping.mapping_dict(),
        action_value_map={},
        validation_status=(
            IpsProfileValidationStatus.VALID
            if valid
            else IpsProfileValidationStatus.UNVALIDATED
        ),
        validated_profile_version=1 if valid else None,
        validation_report={},
    )
    session.add(profile)
    await session.flush()
    return profile, source


@contextmanager
def counting(session):  # type: ignore[no-untyped-def]
    statements: list[str] = []

    def record(*args):  # type: ignore[no-untyped-def]
        statements.append(args[2])

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


class TestFleetAndStates:
    async def test_one_keyed_plan_covers_the_valid_fleet_and_skips_invalid(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        _, first = await add_source(db_session, instance, 412)
        _, second = await add_source(db_session, instance, 413)
        await add_source(db_session, instance, 414, valid=False)
        provider = MockQRadarProvider(now=NOW)

        report = await IpsCollector(
            db_session, provider, settings=cfg(), clock=lambda: NOW
        ).collect(instance)

        assert report.sources_planned == report.sources_collected == 2
        assert report.skipped == {"current profile version is not validated": 1}
        # Six mapped dimensions, each one fleet-wide keyed search. ACTION is unmapped.
        assert len(provider.ips_searches) == 6
        assert {search.qradar_log_source_ids for search in provider.ips_searches} == {
            (412, 413)
        }
        buckets = (await db_session.scalars(select(IpsSourceBucket))).all()
        assert {bucket.log_source_id for bucket in buckets} == {first.id, second.id}
        invalid_wm = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == watermark_collector(414)
            )
        )
        assert invalid_wm.collection_metadata["last_skip_reason"]

    async def test_missing_totals_writes_no_bucket_and_holds_watermark(self, db_session) -> None:
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, 412)
        provider = MockQRadarProvider(now=NOW)
        provider.ips_fault = lambda search: (
            ProviderError("query text must not be stored")
            if search.dimension is IpsDimension.TOTALS
            else None
        )
        report = await IpsCollector(
            db_session, provider, settings=cfg(), clock=lambda: NOW
        ).collect(instance)

        assert report.buckets_written == 0
        missing = await db_session.get(
            IpsSourceBucket, (source.id, NOW.replace(minute=0, second=0))
        )
        assert missing is None
        watermark = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == watermark_collector(412)
            )
        )
        assert watermark.watermark_at is None

    @pytest.mark.parametrize(
        ("dimension", "error", "state"),
        [
            (IpsDimension.DESTINATION_IP, ProviderError("failed"), IpsCellState.FAILED),
            (
                IpsDimension.SIGNATURE,
                ProviderQueryRejectedError("rejected AQL"),
                IpsCellState.REJECTED,
            ),
        ],
    )
    async def test_failed_dimensions_are_nullable_and_do_not_hold_complete_totals(
        self, db_session, dimension, error, state
    ) -> None:
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, 412)
        provider = MockQRadarProvider(now=NOW)
        provider.ips_fault = lambda search: error if search.dimension is dimension else None
        await IpsCollector(db_session, provider, settings=cfg(), clock=lambda: NOW).collect(
            instance
        )
        bucket = (await db_session.scalars(select(IpsSourceBucket))).one()
        prefix = dimension.value.lower()
        assert getattr(bucket, f"{prefix}_measurement") == state.value
        assert getattr(bucket, f"{prefix}_coverage_ratio") is None
        watermark = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == watermark_collector(source.qradar_id)
            )
        )
        assert watermark.watermark_at == bucket.bucket_start

    async def test_budget_exhaustion_after_totals_retries_the_whole_bucket(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, 412)
        boundary = NOW.replace(minute=5, second=0, microsecond=0)
        original_watermark = boundary - BUCKET * 3
        watermark = CollectionWatermark(
            instance_id=instance.id,
            collector=watermark_collector(source.qradar_id),
            watermark_at=original_watermark,
        )
        db_session.add(watermark)
        await db_session.flush()
        provider = MockQRadarProvider(now=NOW)
        provider.ips_force_truncated = lambda search: (
            search.dimension is IpsDimension.DESTINATION_PORT
            and search.window_end - search.window_start > BUCKET
        )
        first = await IpsCollector(
            db_session,
            provider,
            settings=cfg(
                collection_max_backfill_intervals=12,
                search_cadence_max_searches_per_tick=6,
            ),
            clock=lambda: NOW,
        ).collect(instance)

        assert first.searches_sent == 6
        assert first.buckets_written == 0
        assert (await db_session.scalars(select(IpsSourceBucket))).all() == []
        await db_session.refresh(watermark)
        assert watermark.watermark_at == original_watermark

        provider.ips_force_truncated = None
        second = await IpsCollector(
            db_session,
            provider,
            settings=cfg(collection_max_backfill_intervals=12),
            clock=lambda: NOW,
        ).collect(instance)
        buckets = (await db_session.scalars(select(IpsSourceBucket))).all()
        assert second.buckets_written == 2
        assert len(buckets) == 2
        for bucket in buckets:
            assert bucket.destination_port_measurement == "EXACT"
            assert bucket.destination_ip_measurement == "EXACT"
            assert bucket.signature_measurement == "EXACT"
            assert bucket.source_ip_measurement == "EXACT"
            assert bucket.port_fanout_measurement == "EXACT"
            assert bucket.action_measurement == "NOT_COLLECTED"
        await db_session.refresh(watermark)
        assert watermark.watermark_at == boundary - BUCKET

    async def test_unmapped_action_is_structural_and_does_not_hold_watermark(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, 412)

        report = await IpsCollector(
            db_session, MockQRadarProvider(now=NOW), settings=cfg(), clock=lambda: NOW
        ).collect(instance)

        bucket = (await db_session.scalars(select(IpsSourceBucket))).one()
        assert report.buckets_written == 1
        assert bucket.action_measurement == IpsCellState.NOT_COLLECTED.value
        assert bucket.action_coverage_ratio is None
        watermark = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == watermark_collector(source.qradar_id)
            )
        )
        assert watermark.watermark_at == bucket.bucket_start

    async def test_window_plan_that_exceeds_remaining_budget_is_not_started(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        _, first = await add_source(db_session, instance, 412)
        _, second = await add_source(db_session, instance, 413)
        boundary = NOW.replace(minute=5, second=0, microsecond=0)
        db_session.add(
            CollectionWatermark(
                instance_id=instance.id,
                collector=watermark_collector(first.qradar_id),
                watermark_at=boundary - BUCKET * 3,
            )
        )
        await db_session.flush()
        provider = MockQRadarProvider(now=NOW)

        report = await IpsCollector(
            db_session,
            provider,
            settings=cfg(
                collection_max_backfill_intervals=12,
                search_cadence_max_searches_per_tick=7,
            ),
            clock=lambda: NOW,
        ).collect(instance)

        assert report.searches_sent == 6
        assert len(provider.ips_searches) == 6
        assert {search.qradar_log_source_ids for search in provider.ips_searches} == {(412,)}
        assert {bucket.log_source_id for bucket in await db_session.scalars(
            select(IpsSourceBucket)
        )} == {first.id}
        second_watermark = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == watermark_collector(second.qradar_id)
            )
        )
        assert second_watermark.watermark_at is None

    async def test_plan_larger_than_tick_budget_is_visible_and_sends_nothing(
        self, db_session, caplog
    ) -> None:
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, 412)
        provider = MockQRadarProvider(now=NOW)

        with caplog.at_level("WARNING", logger="app.collectors.ips_collector"):
            report = await IpsCollector(
                db_session,
                provider,
                settings=cfg(search_cadence_max_searches_per_tick=5),
                clock=lambda: NOW,
            ).collect(instance)

        assert report.searches_sent == 0
        assert provider.ips_searches == []
        assert report.budget_blocked_windows == [
            {
                "window_start": "2026-10-13T10:00:00+00:00",
                "window_end": "2026-10-13T10:05:00+00:00",
                "source_count": 1,
                "searches_required": 6,
                "per_tick_budget": 5,
                "reason": "plan needs 6 searches; per-tick budget is 5",
                "remedy": "raise search_cadence_max_searches_per_tick to at least 6",
            }
        ]
        assert "plan needs 6 searches; per-tick budget is 5" in caplog.text
        watermark = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == watermark_collector(source.qradar_id)
            )
        )
        assert watermark.watermark_at is None
        assert watermark.collection_metadata["budget_blocked_reason"] == (
            "plan needs 6 searches; per-tick budget is 5"
        )

    async def test_oversized_old_window_does_not_starve_a_later_fitting_window(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        _, first = await add_source(db_session, instance, 412)
        _, second = await add_source(db_session, instance, 413)
        _, later = await add_source(db_session, instance, 414)
        boundary = NOW.replace(minute=5, second=0, microsecond=0)
        original_watermark = boundary - BUCKET * 3
        old_watermarks = [
            CollectionWatermark(
                instance_id=instance.id,
                collector=watermark_collector(source.qradar_id),
                watermark_at=original_watermark,
            )
            for source in (first, second)
        ]
        db_session.add_all(old_watermarks)
        await db_session.flush()
        provider = MockQRadarProvider(now=NOW)

        report = await IpsCollector(
            db_session,
            provider,
            settings=cfg(
                collection_max_backfill_intervals=12,
                ips_max_sources_per_search=1,
                search_cadence_max_searches_per_tick=6,
            ),
            clock=lambda: NOW,
        ).collect(instance)

        assert report.searches_sent == 6
        assert len(provider.ips_searches) == 6
        assert {search.qradar_log_source_ids for search in provider.ips_searches} == {(414,)}
        assert report.budget_blocked_windows[0]["searches_required"] == 12
        assert {bucket.log_source_id for bucket in await db_session.scalars(
            select(IpsSourceBucket)
        )} == {later.id}
        for watermark in old_watermarks:
            await db_session.refresh(watermark)
            assert watermark.watermark_at == original_watermark
            assert watermark.collection_metadata["budget_blocked_reason"] == (
                "plan needs 12 searches; per-tick budget is 6"
            )
        later_watermark = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == watermark_collector(later.qradar_id)
            )
        )
        assert later_watermark.watermark_at == boundary - BUCKET

    async def test_lower_bound_and_maintenance_propagate(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_source(db_session, instance, 412, maintenance_mode=True)
        provider = MockQRadarProvider(now=NOW)
        provider.ips_force_truncated = (
            lambda search: search.dimension is IpsDimension.SOURCE_IP
        )
        await IpsCollector(db_session, provider, settings=cfg(), clock=lambda: NOW).collect(
            instance
        )
        bucket = (await db_session.scalars(select(IpsSourceBucket))).one()
        assert bucket.source_ip_measurement == "LOWER_BOUND"
        assert bucket.distinct_sources_lower_bound is True
        assert bucket.source_ip_coverage_lower_bound is True
        assert bucket.in_maintenance is True
        assert len(bucket.contributors.get("SOURCE_IP", [])) <= 10

    async def test_coverage_above_one_is_clamped(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_source(db_session, instance, 412)

        class Provider(MockQRadarProvider):
            async def get_ips_keyed(self, dimension, **kwargs):  # type: ignore[no-untyped-def, override]
                result = await super().get_ips_keyed(dimension, **kwargs)
                if dimension is not IpsDimension.DESTINATION_IP:
                    return result
                return result.model_copy(
                    update={
                        "rows": tuple(
                            row.model_copy(update={"events": row.events * 100})
                            for row in result.rows
                        )
                    }
                )

        await IpsCollector(
            db_session, Provider(now=NOW), settings=cfg(), clock=lambda: NOW
        ).collect(instance)
        bucket = (await db_session.scalars(select(IpsSourceBucket))).one()
        assert bucket.destination_ip_coverage_ratio == 1.0

    async def test_sources_with_different_watermarks_never_share_a_window(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        _, first = await add_source(db_session, instance, 412)
        _, second = await add_source(db_session, instance, 413)
        boundary = NOW.replace(minute=5, second=0, microsecond=0)
        db_session.add_all(
            [
                CollectionWatermark(
                    instance_id=instance.id,
                    collector=watermark_collector(412),
                    watermark_at=boundary - BUCKET * 3,
                ),
                CollectionWatermark(
                    instance_id=instance.id,
                    collector=watermark_collector(413),
                    watermark_at=boundary - BUCKET * 2,
                ),
            ]
        )
        await db_session.flush()
        provider = MockQRadarProvider(now=NOW)
        await IpsCollector(
            db_session,
            provider,
            settings=cfg(collection_max_backfill_intervals=12),
            clock=lambda: NOW,
        ).collect(instance)
        totals = [s for s in provider.ips_searches if s.dimension is IpsDimension.TOTALS]
        assert len(totals) == 2
        assert {s.qradar_log_source_ids for s in totals} == {(412,), (413,)}
        assert {b.log_source_id for b in (await db_session.scalars(select(IpsSourceBucket)))} == {
            first.id,
            second.id,
        }

    async def test_pipeline_lock_is_held_while_the_provider_and_writes_run(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        await add_source(db_session, instance, 412)
        lock_key = collector_lock_key(instance.id, ips_pipeline_lock_key(412))
        checked = 0

        class Provider(MockQRadarProvider):
            async def get_ips_keyed(self, dimension, **kwargs):  # type: ignore[no-untyped-def, override]
                nonlocal checked
                async with db_session.bind.connect() as connection:
                    acquired = await connection.scalar(
                        text("SELECT pg_try_advisory_lock(:ns, :key)"),
                        {"ns": cfg().collection_advisory_lock_namespace, "key": lock_key},
                    )
                    if acquired:
                        await connection.execute(
                            text("SELECT pg_advisory_unlock(:ns, :key)"),
                            {"ns": cfg().collection_advisory_lock_namespace, "key": lock_key},
                        )
                    assert acquired is False
                    checked += 1
                return await super().get_ips_keyed(dimension, **kwargs)

        await IpsCollector(
            db_session, Provider(now=NOW), settings=cfg(), clock=lambda: NOW
        ).collect(instance)
        assert checked == 6


class TestAdmissionAndScale:
    async def test_admission_quota_refusal_reactivation_and_retirement(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_source(db_session, instance, 412)
        settings = cfg(
            ips_max_new_entities_per_bucket=1,
            ips_min_admission_events=1,
            ips_entity_active_days=1,
        )
        provider = MockQRadarProvider(now=NOW)
        await IpsCollector(db_session, provider, settings=settings, clock=lambda: NOW).collect(
            instance
        )
        bucket = (await db_session.scalars(select(IpsSourceBucket))).one()
        assert all(value <= 1 for value in bucket.admitted.values())
        assert any(value > 0 for value in bucket.rejected_over_quota.values())
        entities = (await db_session.scalars(select(IpsEntity))).all()
        assert len(entities) == sum(bucket.admitted.values())

        retired = entities[0]
        retired.active = False
        retired.retired_at = NOW
        await db_session.execute(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == watermark_collector(412)
            )
        )
        watermark = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == watermark_collector(412)
            )
        )
        watermark.watermark_at = watermark.watermark_at - BUCKET
        await db_session.flush()
        second = await IpsCollector(
            db_session, provider, settings=settings, clock=lambda: NOW
        ).collect(instance)
        assert second.entities_reactivated >= 1
        assert retired.first_seen_at <= retired.last_seen_at

        stale = IpsEntity(
            instance_id=instance.id,
            log_source_id=retired.log_source_id,
            dimension=IpsDimension.SIGNATURE,
            value_key="qid:99999998",
            first_seen_at=NOW - timedelta(days=3),
            last_seen_at=NOW - timedelta(days=2),
            active=True,
            normalizer_version=1,
        )
        db_session.add(stale)
        await db_session.flush()
        later = NOW + BUCKET
        result = await IpsCollector(
            db_session, provider, settings=settings, clock=lambda: later
        ).collect(instance)
        assert result.entities_retired >= 1

    async def test_recollection_replaces_entity_metrics(self, db_session) -> None:
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, 412)
        provider = MockQRadarProvider(now=NOW)
        collector = IpsCollector(db_session, provider, settings=cfg(), clock=lambda: NOW)
        await collector.collect(instance)
        bucket = (await db_session.scalars(select(IpsSourceBucket))).one()
        entity = (await db_session.scalars(select(IpsEntity))).first()
        stale_id = uuid.uuid4()
        await db_session.execute(
            insert(IpsEntity).values(
                id=stale_id,
                instance_id=instance.id,
                log_source_id=source.id,
                dimension=IpsDimension.SIGNATURE.value,
                value_key="qid:99999999",
                first_seen_at=bucket.bucket_start,
                last_seen_at=bucket.bucket_start,
                active=True,
                normalizer_version=1,
            )
        )
        await db_session.execute(
            insert(IpsEntityMetric).values(
                ips_entity_id=stale_id,
                bucket_start=bucket.bucket_start,
                events=999,
                bucket_seconds=300,
            )
        )
        watermark = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == watermark_collector(412)
            )
        )
        watermark.watermark_at -= BUCKET
        await db_session.flush()
        await IpsCollector(db_session, provider, settings=cfg(), clock=lambda: NOW).collect(
            instance
        )
        assert await db_session.get(IpsEntityMetric, (stale_id, bucket.bucket_start)) is None
        assert await db_session.get(IpsEntityMetric, (entity.id, bucket.bucket_start)) is not None

    @pytest.mark.parametrize(("sources", "seeded_entities"), [(2, 10), (20, 400)])
    async def test_statement_count_fixture(
        self, db_session, sources: int, seeded_entities: int
    ) -> None:
        instance = await make_instance(db_session)
        made = [await add_source(db_session, instance, 500 + index) for index in range(sources)]
        source_ids = [source.id for _, source in made]
        if seeded_entities:
            await db_session.execute(
                insert(IpsEntity),
                [
                    {
                        "id": uuid.uuid4(),
                        "instance_id": instance.id,
                        "log_source_id": source_ids[index % len(source_ids)],
                        "dimension": IpsDimension.SIGNATURE.value,
                        "value_key": f"qid:{8_000_000 + index}",
                        "first_seen_at": NOW - timedelta(days=1),
                        "last_seen_at": NOW - timedelta(hours=1),
                        "active": True,
                        "normalizer_version": 1,
                    }
                    for index in range(seeded_entities)
                ],
            )
        await db_session.flush()
        with counting(db_session) as statements:
            await IpsCollector(
                db_session, MockQRadarProvider(now=NOW), settings=cfg(), clock=lambda: NOW
            ).collect(instance)
        # Two versus twenty sources and ten versus four hundred entities: the
        # same fixed load/write/lock shape, with no query per source or entity.
        assert len(statements) == 12
