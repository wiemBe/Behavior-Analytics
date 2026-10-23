"""Catch-all sender collection against a real database (T-069)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, text

from app.catchall.keyed_rows import parse_catchall_rows  # noqa: F401 (contract is exercised)
from app.collectors.catchall_collector import COLLECTOR_NAME, CatchallCollector
from app.core.config import get_settings
from app.models.alert import Alert
from app.models.catchall import CatchallBucket, CatchallSender
from app.models.enums import CatchallMeasurement, CatchallReviewStatus, SearchFamily, Severity
from app.models.monitoring import CollectionWatermark
from app.models.search_cadence import SearchCadenceSetting
from app.providers.base import ProviderError
from app.providers.mock import MockCatchallSearch, MockQRadarProvider
from app.services.ariel_permit import ArielPermitUnavailable
from app.services.catchall_retention import CatchallRetention
from tests.integration.factories import make_instance, make_log_source

pytestmark = pytest.mark.integration

BUCKET = 300
NOW = datetime(2026, 10, 13, 12, 10, tzinfo=UTC)


@pytest_asyncio.fixture
async def pin_cadence(db_session):  # type: ignore[no-untyped-def]
    """The pre-D-28 one-hour cadence (12 buckets per tick) these scenarios were written for."""
    db_session.add(
        SearchCadenceSetting(
            family=SearchFamily.CATCHALL_SENDERS.value, cadence_seconds=3600, updated_at=NOW
        )
    )
    await db_session.commit()
#: With the default 120 s settle, the run at NOW closes at 12:05, so its first-run
#: window is the twelve buckets 11:05 .. 12:00.
FIRST = datetime(2026, 10, 13, 11, 5, tzinfo=UTC)
LAST = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
SRC = 1001


def key_of(moment: datetime) -> int:
    return int(moment.timestamp()) // BUCKET


def row(sender: str | None, events: int = 10, source: int = SRC, records: int = 1) -> dict:
    return {"logsourceid": source, "sender": sender, "events": events, "records": records}


class Script(MockQRadarProvider):
    """A mock whose senders are `script(bucket_start)`; searches are kept."""

    def __init__(self, script) -> None:  # type: ignore[no-untyped-def]
        super().__init__(seed=1, now=NOW)
        self.script = script
        self.catchall_script = lambda key: script(
            datetime.fromtimestamp(key * BUCKET, tz=UTC)
        )


def collector(session, provider, *, now=NOW, **overrides) -> CatchallCollector:
    settings = get_settings().model_copy(
        update={"search_cadence_max_searches_per_tick": 50, **overrides}
    )
    return CatchallCollector(session, provider, settings=settings, clock=lambda: now)


async def setup(session, *, extra_source: bool = False):  # type: ignore[no-untyped-def]
    inst = await make_instance(session)
    source = await make_log_source(session, inst, qradar_id=SRC, name="SIM Generic", type_id=67)
    other = None
    if extra_source:
        other = await make_log_source(session, inst, qradar_id=2002, name="Other", type_id=12)
    return inst, source, other


@contextmanager
def counting(session):  # type: ignore[no-untyped-def]
    statements: list[str] = []
    engine = session.bind.sync_engine

    def record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def senders(session) -> dict[str, CatchallSender]:  # type: ignore[no-untyped-def]
    rows = (
        await session.scalars(
            select(CatchallSender).execution_options(populate_existing=True)
        )
    ).all()
    return {r.identifier: r for r in rows}


async def watermark(session, inst) -> CollectionWatermark:  # type: ignore[no-untyped-def]
    return await session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == inst.id,
            CollectionWatermark.collector == COLLECTOR_NAME,
        )
    )


def steady(sender_rows):  # type: ignore[no-untyped-def]
    return lambda start: sender_rows


@pytest.mark.usefixtures("pin_cadence")
class TestBootstrap:
    async def test_the_first_run_leaves_no_new_sender(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        provider = Script(steady([row("fw-01", 50), row("fw-02", 20)]))

        report = await collector(db_session, provider).collect(inst)

        found = await senders(db_session)
        assert set(found) == {"fw-01", "fw-02"}
        assert {s.review_status for s in found.values()} == {CatchallReviewStatus.REVIEWED.value}
        assert {s.reviewed_by for s in found.values()} == {"bootstrap"}
        assert report.bootstrap and report.senders_created == 2 and report.alerts_raised == 0
        assert report.buckets_complete == 12 and report.bucket_rows_written == 12
        wm = await watermark(db_session, inst)
        assert wm.watermark_at == LAST and wm.intervals_collected == 12
        assert wm.consecutive_failures == 0 and wm.last_error is None

    async def test_a_sender_after_the_bootstrap_is_new(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        await collector(db_session, Script(steady([row("old", 50)]))).collect(inst)
        later = NOW + timedelta(hours=1)
        provider = Script(steady([row("old", 50), row("newcomer", 9)]))

        report = await collector(db_session, provider, now=later).collect(inst)

        found = await senders(db_session)
        assert not report.bootstrap
        assert found["old"].review_status == CatchallReviewStatus.REVIEWED.value
        assert found["newcomer"].review_status == CatchallReviewStatus.NEW.value
        assert found["newcomer"].reviewed_by is None and found["newcomer"].reviewed_at is None
        assert found["newcomer"].first_seen_at == LAST + timedelta(seconds=BUCKET)

    async def test_a_failed_first_run_does_not_end_the_bootstrap(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        failing = Script(steady([row("a", 9)]))
        failing.catchall_fault = lambda s: ProviderError("down")
        failed = await collector(db_session, failing).collect(inst)
        assert failed.buckets_failed == 12 and await senders(db_session) == {}

        report = await collector(db_session, Script(steady([row("a", 9)]))).collect(inst)

        assert report.bootstrap
        found = await senders(db_session)
        assert found["a"].review_status == CatchallReviewStatus.REVIEWED.value

    async def test_a_partial_first_run_keeps_bootstrapping(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        provider = Script(steady([row("a", 9)]))
        bad = FIRST + timedelta(seconds=11 * BUCKET)
        provider.catchall_force_truncated = lambda s: s.window_end - s.window_start > timedelta(
            seconds=BUCKET
        )
        provider.catchall_fault = lambda s: (
            ProviderError("down") if s.window_start == bad else None
        )
        first = await collector(db_session, provider).collect(inst)
        assert first.partial_failure

        late = Script(steady([row("a", 9), row("b", 9)]))
        report = await collector(db_session, late).collect(inst)

        assert report.bootstrap
        assert (await senders(db_session))["b"].review_status == CatchallReviewStatus.REVIEWED.value

    async def test_bootstrap_ends_after_a_clean_run(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        await collector(db_session, Script(steady([row("a", 9)]))).collect(inst)
        wm = await watermark(db_session, inst)
        assert wm.collection_metadata["bootstrapped"] is True


class TestAlerts:
    async def _after_bootstrap(self, db_session, **overrides):  # type: ignore[no-untyped-def]
        inst, source, _ = await setup(db_session)
        await collector(db_session, Script(steady([row("old", 50)])), **overrides).collect(inst)
        return inst, source

    async def test_alerts_are_off_by_default(self, db_session) -> None:
        assert get_settings().catchall_new_sender_alerts_enabled is False
        inst, _ = await self._after_bootstrap(db_session)
        provider = Script(steady([row("old", 50), row("newcomer", 9)]))

        report = await collector(db_session, provider, now=NOW + timedelta(hours=1)).collect(inst)

        assert report.senders_created == 1 and report.alerts_raised == 0
        assert await db_session.scalar(select(func.count()).select_from(Alert)) == 0

    async def test_the_setting_turns_a_low_inventory_alert_on_and_it_is_not_repeated(
        self, db_session
    ) -> None:
        inst, source = await self._after_bootstrap(
            db_session, catchall_new_sender_alerts_enabled=True
        )
        assert await db_session.scalar(select(func.count()).select_from(Alert)) == 0
        provider = Script(steady([row("old", 50), row("newcomer", 9)]))
        later = NOW + timedelta(hours=1)

        report = await collector(
            db_session, provider, now=later, catchall_new_sender_alerts_enabled=True
        ).collect(inst)

        assert report.alerts_raised == 1
        alert = await db_session.scalar(select(Alert))
        assert alert.severity == Severity.LOW and alert.source_type == "catchall_sender"
        assert "newcomer" in alert.title and "SIM Generic" in alert.title
        from app.alerts.fingerprint import catchall_sender_fingerprint

        assert alert.fingerprint == catchall_sender_fingerprint(inst.id, source.id, "newcomer")
        # Re-collecting the same buckets raises no second alert.
        wm = await watermark(db_session, inst)
        wm.watermark_at = LAST
        await db_session.flush()
        again = await collector(
            db_session, provider, now=later, catchall_new_sender_alerts_enabled=True
        ).collect(inst)
        assert again.alerts_raised == 0
        assert await db_session.scalar(select(func.count()).select_from(Alert)) == 1

    async def test_the_bootstrap_raises_no_alert_even_when_alerts_are_on(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        report = await collector(
            db_session,
            Script(steady([row("a", 9), row("b", 9)])),
            catchall_new_sender_alerts_enabled=True,
        ).collect(inst)
        assert report.senders_created == 2 and report.alerts_raised == 0
        assert await db_session.scalar(select(func.count()).select_from(Alert)) == 0

    async def test_the_fingerprint_keeps_the_case_of_the_sender(self) -> None:
        from app.alerts.fingerprint import catchall_sender_fingerprint as fp

        assert fp("i", "s", "FW") != fp("i", "s", "fw")
        assert fp("i", "s", "fw") == fp("i", "s", "fw")
        assert fp("i", "s", "fw") != fp("i", "t", "fw")


@pytest.mark.usefixtures("pin_cadence")
class TestSplitAndFailure:
    async def test_a_truncated_wide_search_is_split_and_nothing_wide_is_kept(
        self, db_session
    ) -> None:
        inst, _source, _ = await setup(db_session)
        provider = Script(steady([row("fw", 10)]))
        provider.catchall_force_truncated = lambda s: s.window_end - s.window_start > timedelta(
            seconds=BUCKET
        )

        report = await collector(db_session, provider).collect(inst)

        assert report.buckets_complete == 12 and report.splits > 0
        assert report.searches_sent == len(provider.catchall_searches) > 1
        buckets = (await db_session.scalars(select(CatchallBucket))).all()
        assert len(buckets) == 12
        assert {b.measurement for b in buckets} == {CatchallMeasurement.EXACT.value}
        assert {b.query_provenance["search_bucket_count"] for b in buckets} == {1}
        assert all(b.events == 10 for b in buckets)
        assert {b.origin for b in buckets} == {"LIVE"}

    async def test_a_live_upsert_keeps_the_origin_of_an_existing_bucket(
        self, db_session
    ) -> None:
        from sqlalchemy import update

        inst, _source, _ = await setup(db_session)
        provider = Script(steady([row("a", 10)]))
        await collector(db_session, provider).collect(inst)
        await db_session.execute(update(CatchallBucket).values(origin="HISTORY"))
        wm = await watermark(db_session, inst)
        wm.watermark_at = FIRST + timedelta(seconds=3 * BUCKET)
        await db_session.flush()

        await collector(db_session, provider).collect(inst)

        buckets = (
            await db_session.scalars(
                select(CatchallBucket).execution_options(populate_existing=True)
            )
        ).all()
        # The re-collected buckets were rewritten and kept their marker; the rest never changed.
        assert {b.origin for b in buckets} == {"HISTORY"}
        assert len(buckets) == 12

    async def test_one_bucket_truncated_is_a_lower_bound_with_its_top_kept(
        self, db_session
    ) -> None:
        inst, _source, _ = await setup(db_session)
        provider = Script(steady([row("fw", 10)]))
        provider.catchall_force_truncated = lambda s: True

        report = await collector(db_session, provider).collect(inst)

        assert report.buckets_lower_bound == 12 and report.buckets_complete == 0
        buckets = (await db_session.scalars(select(CatchallBucket))).all()
        assert {b.measurement for b in buckets} == {CatchallMeasurement.LOWER_BOUND.value}
        assert all(b.distinct_senders_lower_bound for b in buckets)
        assert all(b.top_senders == [{"identifier": "fw", "events": 10}] for b in buckets)
        wm = await watermark(db_session, inst)
        assert wm.watermark_at == LAST

    async def test_a_lower_bound_bucket_writes_no_zero_for_a_source_with_no_rows(
        self, db_session
    ) -> None:
        inst, source, _ = await setup(db_session)
        await make_log_source(db_session, inst, qradar_id=1002, name="Universal", type_id=39)
        provider = Script(steady([row("fw", 10)]))  # only source 1001 has rows
        provider.catchall_force_truncated = lambda s: True
        await collector(db_session, provider).collect(inst)
        by_source = (
            await db_session.execute(
                select(CatchallBucket.catchall_log_source_id, func.count()).group_by(
                    CatchallBucket.catchall_log_source_id
                )
            )
        ).all()
        assert dict(by_source) == {source.id: 12}  # the other source's rows may be in the tail

    async def test_a_complete_bucket_gives_a_measured_zero_to_a_quiet_source(
        self, db_session
    ) -> None:
        inst, _source, _ = await setup(db_session)
        quiet = await make_log_source(db_session, inst, qradar_id=1002, name="Quiet", type_id=39)
        await collector(db_session, Script(steady([row("fw", 10)]))).collect(inst)
        zero = (
            await db_session.scalars(
                select(CatchallBucket).where(CatchallBucket.catchall_log_source_id == quiet.id)
            )
        ).all()
        assert len(zero) == 12
        assert all(b.events == 0 and b.top_senders == [] for b in zero)
        assert all(b.measurement == CatchallMeasurement.EXACT.value for b in zero)

    @pytest.mark.parametrize(
        "failure",
        [ProviderError("QRadar is unreachable"), ArielPermitUnavailable(30, 3, 6)],
    )
    async def test_a_failure_writes_no_bucket_and_the_watermark_holds(
        self, db_session, failure
    ) -> None:
        inst, _source, _ = await setup(db_session)
        await collector(db_session, Script(steady([row("fw", 10)]))).collect(inst)
        held = (await watermark(db_session, inst)).watermark_at
        assert held == LAST
        later = NOW + timedelta(hours=1)
        provider = Script(steady([row("fw", 10), row("zzz", 9)]))
        provider.catchall_fault = lambda s: failure
        before = await db_session.scalar(select(func.count()).select_from(CatchallBucket))

        report = await collector(db_session, provider, now=later).collect(inst)

        assert report.buckets_failed == 12 and report.partial_failure
        assert await db_session.scalar(select(func.count()).select_from(CatchallBucket)) == before
        assert "zzz" not in await senders(db_session)
        wm = await watermark(db_session, inst)
        assert wm.watermark_at == held and wm.consecutive_failures == 1
        assert wm.last_error and "FAILED" in wm.last_error

    async def test_the_watermark_holds_at_the_last_contiguous_bucket(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        provider = Script(steady([row("fw", 10)]))
        bad = FIRST + timedelta(seconds=5 * BUCKET)
        provider.catchall_force_truncated = lambda s: s.window_end - s.window_start > timedelta(
            seconds=BUCKET
        )
        provider.catchall_fault = lambda s: (
            ProviderError("down") if s.window_start == bad else None
        )

        report = await collector(db_session, provider).collect(inst)

        wm = await watermark(db_session, inst)
        assert wm.watermark_at == FIRST + timedelta(seconds=4 * BUCKET)
        assert wm.intervals_collected == 5
        assert report.buckets_failed == 1 and report.buckets_complete == 11
        starts = {
            b.bucket_start for b in (await db_session.scalars(select(CatchallBucket))).all()
        }
        assert bad not in starts and len(starts) == 11
        # The next run starts from the gap and fills it.
        retry = await collector(db_session, Script(steady([row("fw", 10)]))).collect(inst)
        assert retry.buckets_attempted == 7 and retry.buckets_failed == 0
        assert (await watermark(db_session, inst)).watermark_at == LAST
        assert await db_session.scalar(select(func.count()).select_from(CatchallBucket)) == 12

    async def test_budget_exhaustion_is_not_collected_never_zero(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        provider = Script(steady([row("fw", 10)]))
        provider.catchall_force_truncated = lambda s: s.window_end - s.window_start > timedelta(
            seconds=BUCKET
        )

        report = await collector(
            db_session, provider, search_cadence_max_searches_per_tick=1
        ).collect(inst)

        assert report.searches_sent == 1 and len(provider.catchall_searches) == 1
        assert report.buckets_not_collected == 12 and report.partial_failure
        assert await db_session.scalar(select(func.count()).select_from(CatchallBucket)) == 0
        wm = await watermark(db_session, inst)
        assert wm.watermark_at is None and wm.consecutive_failures == 1

    async def test_a_partial_budget_keeps_the_buckets_it_measured(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        provider = Script(steady([row("fw", 10)]))
        provider.catchall_force_truncated = lambda s: s.window_end - s.window_start > timedelta(
            seconds=6 * BUCKET
        )
        # 12 -> 6 + 6; a budget of 3 measures the first half and the second only if it fits.
        report = await collector(
            db_session, provider, search_cadence_max_searches_per_tick=2
        ).collect(inst)
        assert report.searches_sent == 2
        assert report.buckets_complete == 6 and report.buckets_not_collected == 6
        wm = await watermark(db_session, inst)
        assert wm.watermark_at == FIRST + timedelta(seconds=5 * BUCKET)


@pytest.mark.usefixtures("pin_cadence")
class TestUnsupportedAndLocked:
    async def test_a_provider_without_ariel_attempts_nothing(self, db_session, monkeypatch) -> None:
        inst, _source, _ = await setup(db_session)
        provider = Script(steady([row("fw", 10)]))
        monkeypatch.setattr(provider, "supports", lambda capability: False)

        report = await collector(db_session, provider).collect(inst)

        assert report.unsupported and provider.catchall_searches == []
        assert await watermark(db_session, inst) is None

    async def test_rows_of_unknown_or_non_catchall_sources_are_not_stored(
        self, db_session
    ) -> None:
        inst, source, _other = await setup(db_session, extra_source=True)
        provider = Script(steady([row("fw", 10), row("ghost", 50, source=9999),
                                  row("waf-host", 50, source=2002)]))

        report = await collector(db_session, provider).collect(inst)

        assert set(await senders(db_session)) == {"fw"}
        assert report.unmapped_rows == 24
        owners = (await db_session.scalars(select(CatchallBucket.catchall_log_source_id))).all()
        assert set(owners) == {source.id}


@pytest.mark.usefixtures("pin_cadence")
class TestAdmissionPersistence:
    async def test_a_refused_sender_is_never_stored_by_name(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        provider = Script(
            steady(
                [row("kept", 90), row("over-quota", 20), row("tiny", 1), row("ctrl\x07", 40),
                 row("x" * 300, 40), row(None, 30)]
            )
        )

        await collector(
            db_session,
            provider,
            catchall_max_new_senders_per_bucket=1,
            catchall_max_active_senders_per_source=1,
        ).collect(inst)

        assert set(await senders(db_session)) == {"kept"}
        bucket = await db_session.scalar(select(CatchallBucket).limit(1))
        assert bucket.admitted in (0, 1)
        everything = await db_session.scalar(
            text("SELECT string_agg(top_senders::text || query_provenance::text, ' ') "
                 "FROM catchall_bucket")
        )
        for refused in ("over-quota", "tiny", "ctrl", "xxxxxxxx"):
            assert refused not in everything
        first = (
            await db_session.scalars(
                select(CatchallBucket).order_by(CatchallBucket.bucket_start)
            )
        ).first()
        assert first.admitted == 1 and first.rejected_over_quota == 1
        assert first.below_admission_threshold == 1
        assert first.untracked_senders == 5  # over-quota, tiny, ctrl, long, None
        assert first.untracked_events == 20 + 1 + 40 + 40 + 30
        assert first.events == 90 + 20 + 1 + 40 + 40 + 30

    async def test_the_top_list_is_bounded_by_the_setting(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        provider = Script(steady([row(f"h{i:02d}", 10 + i) for i in range(15)]))

        await collector(db_session, provider, catchall_top_senders=4).collect(inst)

        buckets = (await db_session.scalars(select(CatchallBucket))).all()
        assert all(len(b.top_senders) == 4 for b in buckets)
        assert buckets[0].top_senders[0] == {"identifier": "h14", "events": 24}
        assert len(await senders(db_session)) == 15

    async def test_the_active_ceiling_holds_across_buckets(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)

        def script(start: datetime) -> list[dict]:
            index = int((start - FIRST).total_seconds()) // BUCKET
            return [row(f"s{index:02d}", 10)]

        await collector(
            db_session, Script(script), catchall_max_active_senders_per_source=3
        ).collect(inst)

        assert len(await senders(db_session)) == 3
        last = (
            await db_session.scalars(
                select(CatchallBucket).order_by(CatchallBucket.bucket_start.desc())
            )
        ).first()
        assert last.rejected_over_quota == 1 and last.untracked_events == 10

    async def test_a_known_sender_is_recorded_even_when_the_quota_is_zero(
        self, db_session
    ) -> None:
        inst, _source, _ = await setup(db_session)
        await collector(db_session, Script(steady([row("watched", 10)]))).collect(inst)
        flood = [row(f"f{i}", 99) for i in range(50)] + [row("watched", 10)]

        await collector(
            db_session,
            Script(steady(flood)),
            now=NOW + timedelta(hours=1),
            catchall_max_new_senders_per_bucket=0,
        ).collect(inst)

        assert set(await senders(db_session)) == {"watched"}
        last = (
            await db_session.scalars(
                select(CatchallBucket).order_by(CatchallBucket.bucket_start.desc())
            )
        ).first()
        assert last.top_senders == [{"identifier": "watched", "events": 10}]


@pytest.mark.usefixtures("pin_cadence")
class TestIdempotence:
    async def test_collecting_the_same_buckets_again_changes_nothing(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        provider = Script(steady([row("a", 10), row("b", 5)]))
        await collector(db_session, provider).collect(inst)
        snapshot = await self._snapshot(db_session)
        wm = await watermark(db_session, inst)
        wm.watermark_at = FIRST + timedelta(seconds=3 * BUCKET)
        await db_session.flush()

        report = await collector(db_session, provider).collect(inst)

        assert report.buckets_attempted == 8
        assert await self._snapshot(db_session) == snapshot
        assert report.senders_created == 0

    @staticmethod
    async def _snapshot(session):  # type: ignore[no-untyped-def]
        buckets = (
            await session.execute(
                select(
                    CatchallBucket.bucket_start,
                    CatchallBucket.events,
                    CatchallBucket.top_senders,
                    CatchallBucket.measurement,
                ).order_by(CatchallBucket.bucket_start)
            )
        ).all()
        found = {
            n: (s.review_status, s.first_seen_at, s.last_seen_at, s.active)
            for n, s in (await senders(session)).items()
        }
        return [tuple(b) for b in buckets], found

    async def test_a_nothing_to_collect_run_sends_no_search(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        provider = Script(steady([row("a", 10)]))
        await collector(db_session, provider).collect(inst)
        sent = len(provider.catchall_searches)

        report = await collector(db_session, provider, now=NOW + timedelta(seconds=30)).collect(
            inst
        )

        assert report.buckets_attempted == 0 and len(provider.catchall_searches) == sent


@pytest.mark.usefixtures("pin_cadence")
class TestLifecycle:
    async def test_a_stale_sender_retires_and_a_new_sighting_reactivates_it(
        self, db_session
    ) -> None:
        inst, _source, _ = await setup(db_session)
        await collector(db_session, Script(steady([row("a", 10)]))).collect(inst)
        later = NOW + timedelta(days=8)

        await collector(
            db_session, Script(steady([row("b", 10)])), now=later
        ).collect(inst)

        found = await senders(db_session)
        assert found["a"].active is False and found["a"].retired_at == later
        assert found["b"].active is True and found["b"].retired_at is None

        again = NOW + timedelta(days=8, hours=1)
        await collector(db_session, Script(steady([row("a", 10)])), now=again).collect(inst)
        found = await senders(db_session)
        assert found["a"].active is True and found["a"].retired_at is None
        # Reactivation is not a new sender: its review state is untouched.
        assert found["a"].review_status == CatchallReviewStatus.REVIEWED.value

    async def test_a_sender_seen_within_the_active_days_is_not_retired(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        await collector(db_session, Script(steady([row("a", 10)]))).collect(inst)

        await collector(
            db_session, Script(steady([row("b", 10)])), now=NOW + timedelta(days=3)
        ).collect(inst)

        found = await senders(db_session)
        assert found["a"].active is True and found["a"].retired_at is None

    async def test_a_dismissed_sender_stays_dismissed_when_it_returns(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        provider = Script(steady([row("a", 10)]))
        await collector(db_session, provider).collect(inst)
        (await senders(db_session))["a"].review_status = CatchallReviewStatus.DISMISSED.value
        await db_session.flush()

        await collector(db_session, provider, now=NOW + timedelta(hours=1)).collect(inst)

        assert (await senders(db_session))["a"].review_status == "DISMISSED"

    async def test_events_24h_is_recomputed_from_the_stored_top_lists(self, db_session) -> None:
        inst, _source, _ = await setup(db_session)
        await collector(
            db_session, Script(steady([row("a", 10), row("b", 4)])), catchall_top_senders=1
        ).collect(inst)
        found = await senders(db_session)
        await db_session.refresh(found["a"])
        await db_session.refresh(found["b"])
        # Twelve buckets of 10 for "a"; "b" never made a top list: unknown, not zero.
        assert found["a"].events_24h == 120
        assert found["b"].events_24h is None


@pytest.mark.usefixtures("pin_cadence")
class TestConstantSql:
    async def _statements(self, session, count: int) -> int:  # type: ignore[no-untyped-def]
        inst, _source, _ = await setup(session)
        provider = Script(steady([row(f"host-{i:02d}", 10 + i) for i in range(count)]))
        with counting(session) as statements:
            report = await collector(
                session, provider, catchall_max_new_senders_per_bucket=100
            ).collect(inst)
        assert report.senders_admitted == count
        assert report.bucket_rows_written == 12
        # Twelve buckets of the source, one statement; the senders, one more.
        assert sum("INSERT INTO catchall_bucket" in s for s in statements) == 1
        assert sum("INSERT INTO catchall_sender" in s for s in statements) == 1
        return len(statements)

    async def test_three_senders_cost_as_many_statements_as_forty(self, db_session) -> None:
        small = await self._statements(db_session, 3)
        # A new instance, a new source, thirteen times the senders.
        db_session.add_all([])
        large = await self._statements(db_session, 40)
        assert small == large

    async def test_the_same_holds_after_the_bootstrap(self, db_session) -> None:
        async def later(count: int) -> int:
            inst, _source, _ = await setup(db_session)
            await collector(db_session, Script(steady([row("seed", 9)]))).collect(inst)
            provider = Script(steady([row(f"n-{i:02d}", 9) for i in range(count)]))
            with counting(db_session) as statements:
                await collector(
                    db_session,
                    provider,
                    now=NOW + timedelta(hours=1),
                    catchall_max_new_senders_per_bucket=100,
                ).collect(inst)
            return len(statements)

        assert await later(3) == await later(40)

    async def test_alerts_cost_a_bounded_few_statements_per_new_sender(self, db_session) -> None:
        # AlertService.apply_bulk opens each new alert in its own savepoint, which is
        # the existing concurrency guard. The cost is per alert, not per bucket or row.
        async def later(count: int, alerts: bool) -> int:
            inst, _source, _ = await setup(db_session)
            await collector(db_session, Script(steady([row("seed", 9)]))).collect(inst)
            provider = Script(steady([row(f"n-{i:02d}", 9) for i in range(count)]))
            with counting(db_session) as statements:
                await collector(
                    db_session,
                    provider,
                    now=NOW + timedelta(hours=1),
                    catchall_max_new_senders_per_bucket=100,
                    catchall_new_sender_alerts_enabled=alerts,
                ).collect(inst)
            return len(statements)

        base = await later(40, False)
        assert await later(40, True) - base <= 4 * 40


@pytest.mark.usefixtures("pin_cadence")
class TestRetention:
    async def test_old_buckets_and_long_retired_senders_go_and_nothing_else(
        self, db_session
    ) -> None:
        inst, source, _ = await setup(db_session)
        await collector(db_session, Script(steady([row("a", 10)]))).collect(inst)
        old = NOW - timedelta(days=40)
        for offset in range(3):
            db_session.add(
                CatchallBucket(
                    catchall_log_source_id=source.id,
                    bucket_start=old + timedelta(seconds=BUCKET * offset),
                    bucket_seconds=BUCKET, origin="LIVE", events=1, records=1, distinct_senders=1,
                    distinct_senders_lower_bound=False, measurement="EXACT", top_senders=[],
                    admitted=0, below_admission_threshold=0, rejected_over_quota=0,
                    untracked_senders=0, untracked_events=0, query_provenance={},
                    collected_at=old,
                )
            )
        def sender(name: str, **kw):  # type: ignore[no-untyped-def]
            return CatchallSender(
                instance_id=inst.id, catchall_log_source_id=source.id, identifier=name,
                first_seen_at=old, last_seen_at=old, review_status="NEW", **kw,
            )

        db_session.add_all(
            [
                sender("retired-long-ago", active=False, retired_at=NOW - timedelta(days=36)),
                sender("retired-recently", active=False, retired_at=NOW - timedelta(days=10)),
                sender("active-but-old", active=True),
            ]
        )
        await db_session.flush()

        settings = get_settings().model_copy(update={"catchall_retention_batch_size": 100})
        report = await CatchallRetention(db_session, settings=settings, clock=lambda: NOW).run()

        assert report.deleted == {"catchall_bucket": 3, "catchall_sender": 1}
        assert report.incomplete == []
        assert set(await senders(db_session)) == {"a", "retired-recently", "active-but-old"}
        assert await db_session.scalar(select(func.count()).select_from(CatchallBucket)) == 12

    async def test_a_backlog_is_worked_down_in_bounded_batches(self, db_session) -> None:
        _inst, source, _ = await setup(db_session)
        old = NOW - timedelta(days=40)
        for offset in range(250):
            db_session.add(
                CatchallBucket(
                    catchall_log_source_id=source.id,
                    bucket_start=old + timedelta(seconds=BUCKET * offset),
                    bucket_seconds=BUCKET, origin="LIVE", events=1, records=1, distinct_senders=1,
                    distinct_senders_lower_bound=False, measurement="EXACT", top_senders=[],
                    admitted=0, below_admission_threshold=0, rejected_over_quota=0,
                    untracked_senders=0, untracked_events=0, query_provenance={},
                    collected_at=old,
                )
            )
        await db_session.flush()
        settings = get_settings().model_copy(
            update={"catchall_retention_batch_size": 100, "catchall_retention_max_batches": 2}
        )

        report = await CatchallRetention(db_session, settings=settings, clock=lambda: NOW).run()

        assert report.deleted["catchall_bucket"] == 200
        assert report.incomplete == ["catchall_bucket"]
        assert await db_session.scalar(select(func.count()).select_from(CatchallBucket)) == 50


def test_the_mock_search_records_the_types(  # a cheap guard on the hook's shape
) -> None:
    assert MockCatchallSearch((67,), NOW, NOW).device_type_ids == (67,)


class TestGate:
    async def test_exactly_one_concurrent_session_claims_a_catchall_slot(self, db_schema) -> None:
        import asyncio

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.core.config import Settings
        from app.models.enums import SearchFamily
        from app.models.search_cadence import SearchCadenceSetting
        from app.services.search_cadence import SearchCadenceService

        engine = create_async_engine(db_schema, future=True)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            session.add(
                SearchCadenceSetting(
                    family=SearchFamily.CATCHALL_SENDERS.value,
                    cadence_seconds=3600,
                    updated_at=NOW,
                )
            )
            await session.commit()

        async def claim(at: datetime) -> datetime | None:
            async with maker() as session:
                claimed = await SearchCadenceService(
                    session, Settings(search_cadence_settle_seconds=120), lambda: at
                ).claim_slot(SearchFamily.CATCHALL_SENDERS)
                await session.commit()
                return claimed

        try:
            first = await asyncio.gather(claim(NOW), claim(NOW), claim(NOW))
            assert sum(value is not None for value in first) == 1
            assert await claim(NOW + timedelta(minutes=10)) is None  # same slot
            assert await claim(NOW + timedelta(hours=1)) is not None  # the next slot
        finally:
            await engine.dispose()

    async def test_the_family_defaults_to_five_minutes_without_a_seed_row(self, db_session) -> None:
        from app.core.config import Settings
        from app.models.enums import SearchFamily
        from app.services.search_cadence import SearchCadenceService

        row = await SearchCadenceService(db_session, Settings(), lambda: NOW).get(
            SearchFamily.CATCHALL_SENDERS
        )
        assert row.cadence_seconds == 300
