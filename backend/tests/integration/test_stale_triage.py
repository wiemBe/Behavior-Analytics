"""SOC-1c (D-55): the stale triage job closes NEW items nobody took, and nothing else.

Every family is driven through the real job against PostgreSQL: the unrowed and the rowed
case, the guards (IN_PROGRESS, CLOSED, a recent reopen, a young anomaly), the race (the
version moving between the candidate read and the write), the constant statement count
and the batch bound, and the API surface (STALE is not selectable, reopen works, the
list filter finds it).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.alert import Alert
from app.models.enums import (
    AlertStatus,
    AnomalyFamily,
    AnomalyState,
    PwScopeKind,
    Severity,
    TriageDisposition,
    TriageEventKind,
    TriageStatus,
)
from app.models.ips import IpsAnomaly
from app.models.log_source import LogSourceAnomaly
from app.models.property_watch import PropertyWatch, PwAnomaly
from app.models.triage import AnomalyTriage, AnomalyTriageEvent
from app.models.waf import WafAnomaly, WafEndpoint
from app.services.stale_triage import StaleTriageCloser, stale_close_note
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_anomaly_triage_api import (
    TRIAGER,
    ips,
    phase_a,
    pw,
    url,
    waf,
)
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the stale triage tests",
    ),
]

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=5)
YOUNG = NOW - timedelta(days=1)
YESTERDAY = NOW - timedelta(days=1)

FAMILIES = [
    AnomalyFamily.LOG_SOURCE,
    AnomalyFamily.WAF,
    AnomalyFamily.IPS,
    AnomalyFamily.PROPERTY_WATCH,
]
MODELS = {
    AnomalyFamily.LOG_SOURCE: LogSourceAnomaly,
    AnomalyFamily.WAF: WafAnomaly,
    AnomalyFamily.IPS: IpsAnomaly,
    AnomalyFamily.PROPERTY_WATCH: PwAnomaly,
}


def _maker():  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def seed_parents(session) -> dict[str, uuid.UUID]:  # type: ignore[no-untyped-def]
    inst = await make_instance(session)
    source = await make_log_source(
        session, inst, qradar_id=9301, name=f"stale-{uuid.uuid4().hex[:6]}"
    )
    endpoint = WafEndpoint(
        instance_id=inst.id,
        log_source_id=source.id,
        normalized_endpoint="/stale",
        http_method="GET",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        first_seen_at=OLD,
        last_seen_at=NOW,
        tracked_since=OLD,
        is_active=True,
    )
    watch = PropertyWatch(
        instance_id=inst.id,
        name=f"watch-{uuid.uuid4().hex[:8]}",
        scope_kind=PwScopeKind.LOG_SOURCE,
        log_source_id=source.id,
        properties=[{"kind": "NORMALIZED", "name": "URL"}],
        detectors=["PW_VALUE_SPIKE"],
    )
    session.add_all([endpoint, watch])
    await session.flush()
    return {"source": source.id, "endpoint": endpoint.id, "watch": watch.id}


def anomaly(family: AnomalyFamily, parents: dict, detected_at: datetime):  # type: ignore[no-untyped-def]
    """A fresh anomaly of `family`. The detector state is deliberately OPEN."""
    kw = {"detected_at": detected_at, "state": AnomalyState.OPEN}
    if family is AnomalyFamily.LOG_SOURCE:
        return phase_a(parents["source"], **kw)
    if family is AnomalyFamily.WAF:
        return waf(parents["endpoint"], **kw)
    if family is AnomalyFamily.IPS:
        return ips(parents["source"], **kw)
    return pw(parents["watch"], value_key=f"/v/{uuid.uuid4().hex}", **kw)


def triage_row(  # type: ignore[no-untyped-def]
    family: AnomalyFamily,
    anomaly_id: uuid.UUID,
    *,
    status: TriageStatus = TriageStatus.NEW,
    updated_at: datetime = OLD,
    version: int = 2,
    owner: str | None = None,
    disposition: TriageDisposition | None = None,
):
    return AnomalyTriage(
        family=family,
        anomaly_id=anomaly_id,
        owner=owner,
        status=status,
        disposition=disposition,
        updated_by="analyst@example.com",
        updated_at=updated_at,
        version=version,
    )


async def close_now(session, **kw):  # type: ignore[no-untyped-def]
    return await StaleTriageCloser(session, days=3, clock=lambda: NOW, **kw).run()


async def triage_of(session, family, anomaly_id):  # type: ignore[no-untyped-def]
    return await session.scalar(
        select(AnomalyTriage).where(
            AnomalyTriage.family == family, AnomalyTriage.anomaly_id == anomaly_id
        )
    )


async def events_of(session, triage_id):  # type: ignore[no-untyped-def]
    return list(
        (
            await session.scalars(
                select(AnomalyTriageEvent).where(AnomalyTriageEvent.triage_id == triage_id)
            )
        ).all()
    )


async def row_dict(session, table: str, row_id: uuid.UUID) -> dict:  # type: ignore[no-untyped-def]
    result = await session.execute(
        text(f"SELECT * FROM {table} WHERE id = :i"), {"i": row_id}  # noqa: S608
    )
    return dict(result.mappings().one())


@pytest.fixture
async def maker(db_schema):  # type: ignore[no-untyped-def]
    engine, make = _maker()
    try:
        yield make
    finally:
        await engine.dispose()


# ------------------------------------------------------------------------- per family
@pytest.mark.asyncio
@pytest.mark.parametrize("family", FAMILIES, ids=lambda f: f.value)
class TestEachFamily:
    async def test_an_old_anomaly_without_a_row_gets_a_closed_stale_row(
        self, maker, family
    ) -> None:
        async with maker() as s:
            parents = await seed_parents(s)
            a = anomaly(family, parents, OLD)
            s.add(a)
            await s.commit()
            anomaly_id = a.id
        async with maker() as s:
            report = await close_now(s)
            assert report.families[family.value] == 1
        async with maker() as s:
            row = await triage_of(s, family, anomaly_id)
            assert row is not None
            assert (row.status, row.disposition) == (
                TriageStatus.CLOSED,
                TriageDisposition.STALE,
            )
            assert row.owner is None
            assert row.version == 1
            assert row.updated_by == "system"
            assert row.disposition_note == stale_close_note(3)
            events = await events_of(s, row.id)
            assert len(events) == 1
            ev = events[0]
            assert (ev.kind, ev.actor, ev.from_value, ev.to_value, ev.triage_version) == (
                TriageEventKind.STATUS,
                "system",
                "NEW",
                "CLOSED",
                1,
            )

    async def test_an_old_new_row_is_closed_and_its_version_moves_by_one(
        self, maker, family
    ) -> None:
        async with maker() as s:
            parents = await seed_parents(s)
            a = anomaly(family, parents, OLD)
            s.add(a)
            await s.flush()
            s.add(triage_row(family, a.id, version=4, owner="kept@example.com"))
            await s.commit()
            anomaly_id = a.id
        async with maker() as s:
            await close_now(s)
        async with maker() as s:
            row = await triage_of(s, family, anomaly_id)
            assert row is not None
            assert row.status == TriageStatus.CLOSED
            assert row.disposition == TriageDisposition.STALE
            assert row.version == 5
            assert row.owner == "kept@example.com"  # no owner change
            assert row.updated_by == "system"
            events = await events_of(s, row.id)
            assert [(e.actor, e.triage_version) for e in events] == [("system", 5)]

    async def test_everything_that_must_stay_stays(self, maker, family) -> None:
        async with maker() as s:
            parents = await seed_parents(s)
            taken = anomaly(family, parents, OLD)
            closed = anomaly(family, parents, OLD)
            reopened = anomaly(family, parents, OLD)
            young = anomaly(family, parents, YOUNG)
            young_row = anomaly(family, parents, YOUNG)
            s.add_all([taken, closed, reopened, young, young_row])
            await s.flush()
            s.add_all(
                [
                    triage_row(
                        family,
                        taken.id,
                        status=TriageStatus.IN_PROGRESS,
                        owner="me@example.com",
                    ),
                    triage_row(
                        family,
                        closed.id,
                        status=TriageStatus.CLOSED,
                        disposition=TriageDisposition.NOISE,
                    ),
                    triage_row(family, reopened.id, updated_at=YESTERDAY),
                    triage_row(family, young_row.id, updated_at=OLD),
                ]
            )
            await s.commit()
        async with maker() as s:
            before = {
                r.id: await row_dict(s, "anomaly_triage", r.id)
                for r in (await s.scalars(select(AnomalyTriage))).all()
            }
            events_before = len((await s.scalars(select(AnomalyTriageEvent))).all())
        async with maker() as s:
            report = await close_now(s)
            assert report.closed == 0
        async with maker() as s:
            after = {
                r.id: await row_dict(s, "anomaly_triage", r.id)
                for r in (await s.scalars(select(AnomalyTriage))).all()
            }
            assert after == before  # no row created for the young unrowed one either
            assert len((await s.scalars(select(AnomalyTriageEvent))).all()) == events_before

    async def test_the_anomaly_and_its_alert_are_byte_for_byte_unchanged(
        self, maker, family
    ) -> None:
        table = MODELS[family].__tablename__
        async with maker() as s:
            parents = await seed_parents(s)
            a = anomaly(family, parents, OLD)
            alert = Alert(
                dedup_key=f"dk:{uuid.uuid4()}",
                fingerprint=f"fp:{uuid.uuid4()}",
                title="open alert",
                severity=Severity.HIGH,
                status=AlertStatus.OPEN,
                source_type="log_source",
                source_id=uuid.uuid4(),
                context={},
                opened_at=OLD,
                first_seen_at=OLD,
                occurrence_count=1,
                evidence_snapshot={},
                source_anomaly_ids=[],
            )
            s.add_all([a, alert])
            await s.commit()
            anomaly_id, alert_id = a.id, alert.id
        async with maker() as s:
            anomaly_before = await row_dict(s, table, anomaly_id)
            alert_before = await row_dict(s, "alert", alert_id)
        async with maker() as s:
            assert (await close_now(s)).families[family.value] == 1
        async with maker() as s:
            assert await row_dict(s, table, anomaly_id) == anomaly_before
            assert await row_dict(s, "alert", alert_id) == alert_before
            assert anomaly_before["state"] == AnomalyState.OPEN.value  # detector untouched

    async def test_a_second_run_is_a_no_op(self, maker, family) -> None:
        async with maker() as s:
            parents = await seed_parents(s)
            a = anomaly(family, parents, OLD)
            s.add(a)
            await s.commit()
            anomaly_id = a.id
        async with maker() as s:
            assert (await close_now(s)).closed == 1
        async with maker() as s:
            first = await row_dict(s, "anomaly_triage", (await triage_of(s, family, anomaly_id)).id)
        async with maker() as s:
            assert (await close_now(s)).closed == 0
        async with maker() as s:
            row = await triage_of(s, family, anomaly_id)
            assert await row_dict(s, "anomaly_triage", row.id) == first
            assert len(await events_of(s, row.id)) == 1

    async def test_an_analyst_who_moves_the_version_first_is_not_overwritten(
        self, maker, family
    ) -> None:
        """The version moves between the candidate read and the write; the job skips it."""
        async with maker() as s:
            parents = await seed_parents(s)
            moved_status = anomaly(family, parents, OLD)
            moved_owner = anomaly(family, parents, OLD)
            s.add_all([moved_status, moved_owner])
            await s.flush()
            s.add_all(
                [
                    triage_row(family, moved_status.id, version=2),
                    triage_row(family, moved_owner.id, version=2),
                ]
            )
            await s.commit()
            status_id, owner_id = moved_status.id, moved_owner.id

        class Racing(StaleTriageCloser):
            async def _write_candidates(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                if args[0] != family:
                    return await super()._write_candidates(*args, **kwargs)
                async with maker() as other:
                    # The analyst takes one item ...
                    await other.execute(
                        text(
                            "UPDATE anomaly_triage SET status = 'IN_PROGRESS', "
                            "owner = 'me@example.com', version = version + 1 "
                            "WHERE anomaly_id = :i"
                        ),
                        {"i": status_id},
                    )
                    # ... and edits the other without leaving NEW (version only).
                    await other.execute(
                        text(
                            "UPDATE anomaly_triage SET owner = 'x@example.com', "
                            "version = version + 1 WHERE anomaly_id = :i"
                        ),
                        {"i": owner_id},
                    )
                    await other.commit()
                return await super()._write_candidates(*args, **kwargs)

        async with maker() as s:
            report = await Racing(s, days=3, clock=lambda: NOW).run()
            assert report.closed == 0
        async with maker() as s:
            taken = await triage_of(s, family, status_id)
            edited = await triage_of(s, family, owner_id)
            assert (taken.status, taken.owner, taken.version) == (
                TriageStatus.IN_PROGRESS,
                "me@example.com",
                3,
            )
            assert (edited.status, edited.disposition, edited.version) == (
                TriageStatus.NEW,
                None,
                3,
            )
            assert await events_of(s, taken.id) == []
            assert await events_of(s, edited.id) == []

    async def test_a_row_created_after_the_read_wins_over_the_insert(
        self, maker, family
    ) -> None:
        async with maker() as s:
            parents = await seed_parents(s)
            a = anomaly(family, parents, OLD)
            s.add(a)
            await s.commit()
            anomaly_id = a.id

        class Racing(StaleTriageCloser):
            async def _write_candidates(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                if args[0] != family:
                    return await super()._write_candidates(*args, **kwargs)
                async with maker() as other:
                    other.add(
                        triage_row(
                            family,
                            anomaly_id,
                            status=TriageStatus.IN_PROGRESS,
                            updated_at=NOW,
                            version=1,
                            owner="me@example.com",
                        )
                    )
                    await other.commit()
                return await super()._write_candidates(*args, **kwargs)

        async with maker() as s:
            assert (await Racing(s, days=3, clock=lambda: NOW).run()).closed == 0
        async with maker() as s:
            row = await triage_of(s, family, anomaly_id)
            assert (row.status, row.owner, row.version) == (
                TriageStatus.IN_PROGRESS,
                "me@example.com",
                1,
            )
            assert await events_of(s, row.id) == []


# ------------------------------------------------------------------- cost and bound
@pytest.mark.asyncio
async def test_the_statement_count_is_the_same_for_1_and_200_eligible(maker) -> None:
    async with maker() as s:
        parents = await seed_parents(s)
        s.add(anomaly(AnomalyFamily.LOG_SOURCE, parents, OLD))
        await s.commit()

    counted = {"n": 0}
    engine = create_async_engine(
        os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool
    )

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count(*args, **kwargs):  # type: ignore[no-untyped-def]
        counted["n"] += 1

    counting = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with counting() as s:
            assert (await close_now(s)).closed == 1
        small = counted["n"]

        async with maker() as s:
            for family in FAMILIES:
                # 50 per family: some unrowed, some with an old NEW row.
                items = [anomaly(family, parents, OLD) for _ in range(50)]
                s.add_all(items)
                await s.flush()
                s.add_all(triage_row(family, a.id) for a in items[:25])
            await s.commit()
        counted["n"] = 0
        async with counting() as s:
            report = await close_now(s)
        assert report.closed == 200 - 0  # 4 x 50 eligible (the earlier one is closed)
        assert counted["n"] == small
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_one_run_is_bounded_and_the_next_run_continues(maker) -> None:
    async with maker() as s:
        parents = await seed_parents(s)
        s.add_all(anomaly(AnomalyFamily.LOG_SOURCE, parents, OLD) for _ in range(12))
        await s.commit()
    totals = []
    for _ in range(4):
        async with maker() as s:
            report = await StaleTriageCloser(
                s, days=3, batch_size=5, clock=lambda: NOW
            ).run()
            totals.append(report.families["log_source"])
    assert totals == [5, 5, 2, 0]


@pytest.mark.asyncio
async def test_zero_days_closes_nothing(maker) -> None:
    async with maker() as s:
        parents = await seed_parents(s)
        s.add(anomaly(AnomalyFamily.LOG_SOURCE, parents, OLD - timedelta(days=400)))
        await s.commit()
    async with maker() as s:
        report = await StaleTriageCloser(s, days=0, clock=lambda: NOW).run()
        assert report.status == "disabled"
    async with maker() as s:
        assert (await s.scalars(select(AnomalyTriage))).all() == []


@pytest.mark.asyncio
async def test_the_window_follows_the_setting(maker) -> None:
    async with maker() as s:
        parents = await seed_parents(s)
        s.add(anomaly(AnomalyFamily.LOG_SOURCE, parents, NOW - timedelta(days=6)))
        s.add(anomaly(AnomalyFamily.LOG_SOURCE, parents, NOW - timedelta(days=2)))
        await s.commit()
    async with maker() as s:
        report = await StaleTriageCloser(s, days=5, clock=lambda: NOW).run()
        assert report.closed == 1
    async with maker() as s:
        row = (await s.scalars(select(AnomalyTriage))).one()
        assert row.disposition_note == stale_close_note(5)


# ----------------------------------------------------------------------------- API
class TestApi:
    @pytest.fixture
    def stale_item(self, db_schema):  # type: ignore[no-untyped-def]
        async def seed(session):  # type: ignore[no-untyped-def]
            parents = await seed_parents(session)
            a = anomaly(AnomalyFamily.LOG_SOURCE, parents, OLD)
            session.add(a)
            await session.flush()
            ids = a.id
            await session.commit()
            await close_now(session)
            return ids

        return run(seed)

    def test_the_read_shows_stale_and_the_note(self, stale_item) -> None:
        body = Client(TRIAGER).get(url("log_source", stale_item)).json()
        assert body["status"] == "CLOSED"
        assert body["disposition"] == "STALE"
        assert body["updated_by"] == "system"
        assert body["version"] == 1
        assert body["disposition_note"] == stale_close_note(3)
        assert body["events"]["items"][0]["actor"] == "system"

    def test_an_analyst_cannot_choose_stale(self, stale_item) -> None:
        client = Client(TRIAGER)
        response = client.patch(
            url("log_source", stale_item),
            json={"disposition": "STALE", "expected_version": 1},
        )
        assert response.status_code == 422
        assert client.get(url("log_source", stale_item)).json()["version"] == 1

    def test_closing_with_stale_is_422_on_an_untouched_item(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            parents = await seed_parents(session)
            a = anomaly(AnomalyFamily.WAF, parents, YOUNG)
            session.add(a)
            await session.flush()
            return a.id

        anomaly_id = run(seed)
        response = Client(TRIAGER).patch(
            url("waf", anomaly_id),
            json={"status": "CLOSED", "disposition": "STALE", "expected_version": 0},
        )
        assert response.status_code == 422

    def test_a_stale_item_can_be_reopened(self, stale_item) -> None:
        client = Client(TRIAGER)
        reopened = client.patch(
            url("log_source", stale_item),
            json={"status": "IN_PROGRESS", "owner": "me@example.com", "expected_version": 1},
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["status"] == "IN_PROGRESS"
        assert reopened.json()["version"] == 2

    def test_the_list_filter_finds_stale(self, stale_item) -> None:
        client = Client(TRIAGER)
        body = client.get(
            "/api/v1/anomalies",
            params={"limit": 200, "disposition": "STALE", "active_only": "true"},
        ).json()
        assert [item["id"] for item in body["items"]] == [str(stale_item)]
        none = client.get(
            "/api/v1/anomalies",
            params={"limit": 200, "disposition": "NOISE", "active_only": "true"},
        ).json()
        assert none["total"] == 0
