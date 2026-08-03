"""Phase B persistence contract, asserted against a real PostgreSQL.

The centrepiece is the nullable-virtual-host uniqueness contract. It cannot be
tested against SQLite or by reading the model: NULLS NOT DISTINCT is a
PostgreSQL 15+ index property, SQLAlchemy carries it only as a dialect kwarg,
and the failure mode if it is silently dropped is not an error -- it is a
duplicate row. Every re-collection would mint a new entity, each holding a
single observation, and no baseline would ever reach its minimum sample count.
The symptom would be "Phase B never opens an anomaly", weeks later, with no
exception anywhere.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import BucketCompleteness
from app.models.waf import WafEndpoint, WafEndpointMetric
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _endpoint(
    session: AsyncSession,
    *,
    instance_id: uuid.UUID,
    log_source_id: uuid.UUID,
    virtual_host: str | None,
    endpoint: str = "/portal/login",
    method: str = "GET",
) -> WafEndpoint:
    now = datetime.now(UTC)
    row = WafEndpoint(
        instance_id=instance_id,
        log_source_id=log_source_id,
        virtual_host=virtual_host,
        normalized_endpoint=endpoint,
        http_method=method,
        normalizer_version=1,
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(row)
    await session.flush()
    return row


class TestNullableVirtualHostIdentity:
    async def test_index_is_declared_nulls_not_distinct(
        self, db_session: AsyncSession
    ) -> None:
        """Assert the property directly, not just its observable effect.

        If a future migration edit drops the clause, this fails with a message
        naming the cause rather than a confusing duplicate-row symptom.
        """
        result = await db_session.execute(
            text(
                "SELECT i.indnullsnotdistinct FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'uq_waf_endpoint_natural_key'"
            )
        )
        assert result.scalar() is True

    async def test_repeated_hostless_observations_are_one_entity(
        self, db_session: AsyncSession
    ) -> None:
        """The contract this whole design decision exists for."""
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        await _endpoint(
            db_session,
            instance_id=instance.id,
            log_source_id=source.id,
            virtual_host=None,
        )

        with pytest.raises(IntegrityError):
            await _endpoint(
                db_session,
                instance_id=instance.id,
                log_source_id=source.id,
                virtual_host=None,
            )
        await db_session.rollback()

    async def test_a_hostless_and_a_hosted_endpoint_are_different_entities(
        self, db_session: AsyncSession
    ) -> None:
        """NULL must not collide with a real host, only with another NULL."""
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        await _endpoint(
            db_session, instance_id=instance.id, log_source_id=source.id,
            virtual_host=None,
        )
        await _endpoint(
            db_session, instance_id=instance.id, log_source_id=source.id,
            virtual_host="portal.lab.test",
        )
        rows = (await db_session.scalars(select(WafEndpoint))).all()
        assert len(rows) == 2

    async def test_different_hosts_sharing_a_path_stay_separate(
        self, db_session: AsyncSession
    ) -> None:
        """The collision the virtual host was added to prevent.

        One WAF appliance fronts many applications through one log source, so
        `/login` on two different hosts must not share a baseline.
        """
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        for host in ("app-a.lab.test", "app-b.lab.test"):
            await _endpoint(
                db_session, instance_id=instance.id, log_source_id=source.id,
                virtual_host=host, endpoint="/login",
            )
        rows = (await db_session.scalars(select(WafEndpoint))).all()
        assert len(rows) == 2

    async def test_method_is_part_of_the_identity(
        self, db_session: AsyncSession
    ) -> None:
        """A POST spike on /login is credential stuffing; a GET spike is scraping."""
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        for method in ("GET", "POST"):
            await _endpoint(
                db_session, instance_id=instance.id, log_source_id=source.id,
                virtual_host=None, endpoint="/login", method=method,
            )
        rows = (await db_session.scalars(select(WafEndpoint))).all()
        assert len(rows) == 2

    async def test_empty_string_virtual_host_is_rejected(
        self, db_session: AsyncSession
    ) -> None:
        """"" is a value. Two hostless applications must not look like a match."""
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        with pytest.raises(IntegrityError):
            await _endpoint(
                db_session, instance_id=instance.id, log_source_id=source.id,
                virtual_host="",
            )
        await db_session.rollback()


class TestMetricUpsertIsIdempotent:
    async def test_re_collecting_a_bucket_overwrites_rather_than_duplicates(
        self, db_session: AsyncSession
    ) -> None:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        endpoint = await _endpoint(
            db_session, instance_id=instance.id, log_source_id=source.id,
            virtual_host=None,
        )
        bucket = datetime(2026, 8, 3, 19, 25, tzinfo=UTC)

        for count in (10, 42):
            stmt = pg_insert(WafEndpointMetric).values(
                waf_endpoint_id=endpoint.id,
                bucket_start=bucket,
                bucket_seconds=300,
                request_count=count,
                endpoint_coverage_ratio=1.0,
                completeness=BucketCompleteness.COMPLETE,
                normalizer_version=1,
            )
            await db_session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["waf_endpoint_id", "bucket_start"],
                    set_={"request_count": stmt.excluded.request_count},
                )
            )
        await db_session.flush()

        rows = (await db_session.scalars(select(WafEndpointMetric))).all()
        assert len(rows) == 1
        assert rows[0].request_count == 42

    async def test_unavailable_counts_stay_null_not_zero(
        self, db_session: AsyncSession
    ) -> None:
        """NULL is "this DSM cannot tell us"; 0 would be a measured absence."""
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        endpoint = await _endpoint(
            db_session, instance_id=instance.id, log_source_id=source.id,
            virtual_host=None,
        )
        metric = WafEndpointMetric(
            waf_endpoint_id=endpoint.id,
            bucket_start=datetime(2026, 8, 3, 19, 25, tzinfo=UTC),
            bucket_seconds=300,
            request_count=120,
            endpoint_coverage_ratio=1.0,
            completeness=BucketCompleteness.COMPLETE,
            normalizer_version=1,
        )
        db_session.add(metric)
        await db_session.flush()

        assert metric.blocked_count is None
        assert metric.error_count is None
        assert metric.distinct_client_count is None

    async def test_negative_request_count_is_rejected(
        self, db_session: AsyncSession
    ) -> None:
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        endpoint = await _endpoint(
            db_session, instance_id=instance.id, log_source_id=source.id,
            virtual_host=None,
        )
        db_session.add(
            WafEndpointMetric(
                waf_endpoint_id=endpoint.id,
                bucket_start=datetime(2026, 8, 3, 19, 25, tzinfo=UTC),
                bucket_seconds=300,
                request_count=-1,
                endpoint_coverage_ratio=1.0,
                completeness=BucketCompleteness.COMPLETE,
                normalizer_version=1,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_bucket_end_is_half_open(self, db_session: AsyncSession) -> None:
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        endpoint = await _endpoint(
            db_session, instance_id=instance.id, log_source_id=source.id,
            virtual_host=None,
        )
        start = datetime(2026, 8, 3, 19, 25, tzinfo=UTC)
        metric = WafEndpointMetric(
            waf_endpoint_id=endpoint.id,
            bucket_start=start,
            bucket_seconds=300,
            request_count=1,
            endpoint_coverage_ratio=1.0,
            completeness=BucketCompleteness.COMPLETE,
            normalizer_version=1,
        )
        db_session.add(metric)
        await db_session.flush()
        assert metric.bucket_end == start + timedelta(seconds=300)
