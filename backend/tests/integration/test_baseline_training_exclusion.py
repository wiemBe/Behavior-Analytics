"""SOC-13c: Phase A training honours analyst exclusions, and fails closed."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.anomaly import training_exclusions as te
from app.anomaly.baseline import BaselineBuilder
from app.anomaly.detectors import detect_volume_spike
from app.anomaly.engine import baseline_cell
from app.core.config import Settings
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    DetectorSignal,
    EvidenceStatus,
    Severity,
)
from app.models.log_source import LogSourceAnomaly, LogSourceBaseline
from app.models.training_exclusion import BaselineTrainingExclusion
from app.security.rbac import Principal
from tests.integration.factories import add_metric, make_instance, make_log_source
from tests.integration.test_fleet_api import Client, run
from tests.integration.test_waf_scale import counting
from tests.unit.test_volume_detectors import _ctx, _point

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

BUCKET = timedelta(seconds=300)
H = timedelta(hours=1)
# 2026-07-01 is a Wednesday.
BASE = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
WEEKS = 12
NOW = BASE + timedelta(weeks=WEEKS)
RECENT = datetime.now(UTC) - timedelta(hours=2)


def _settings() -> Settings:
    return Settings(encryption_key="x" * 44, baseline_min_samples=8, baseline_lookback_days=120)


async def _series(session, ls, *, hour: int = 10, values: list[float] | None = None):  # type: ignore[no-untyped-def]
    values = values or [50.0] * WEEKS
    base = BASE.replace(hour=hour)
    for week, eps in enumerate(values):
        await add_metric(
            session, ls, base + timedelta(weeks=week), average_eps=eps, event_count=int(eps * 300)
        )
    return [base + timedelta(weeks=week) for week in range(WEEKS)]


def _exclusion(source_id, start, end=None, **extra):  # type: ignore[no-untyped-def]
    values = {
        "origin_family": "log_source",
        "origin_anomaly_id": uuid.uuid4(),
        "subject_kind": "log_source",
        "subject_id": source_id,
        "scope_key": te.LOG_SOURCE_VOLUME_SCOPE,
        "starts_at": start,
        "ends_at": end or start + BUCKET,
        "reason": "outage",
        "created_by": "ada",
    }
    values.update(extra)
    return BaselineTrainingExclusion(**values)


async def _cell(session, source_id, metric="average_eps", hour=10):  # type: ignore[no-untyped-def]
    return await session.scalar(
        select(LogSourceBaseline).where(
            LogSourceBaseline.log_source_id == source_id,
            LogSourceBaseline.metric_name == metric,
            LogSourceBaseline.weekday == 3,
            LogSourceBaseline.hour == hour,
        )
    )


async def _rebuild(session, *sources):  # type: ignore[no-untyped-def]
    await BaselineBuilder(session, settings=_settings()).rebuild_many(sources, now=NOW)


async def test_an_excluded_interval_changes_the_band(db_session) -> None:
    inst = await make_instance(db_session)
    plain = await make_log_source(db_session, inst, qradar_id=1, name="plain")
    excluded = await make_log_source(db_session, inst, qradar_id=2, name="excluded")
    values = [50.0, 52.0, 48.0, 51.0, 49.0, 50.0, 52.0, 48.0, 51.0, 49.0, 50.0, 900.0]
    await _series(db_session, plain, values=values)
    buckets = await _series(db_session, excluded, values=values)
    db_session.add(_exclusion(excluded.id, buckets[-1]))
    await db_session.flush()

    await _rebuild(db_session, plain, excluded)

    without = await _cell(db_session, plain.id)
    with_it = await _cell(db_session, excluded.id)
    assert without.sample_count == 12 and with_it.sample_count == 11
    assert 900.0 in without.observations and 900.0 not in with_it.observations
    assert with_it.p95 < without.p95
    assert with_it.exclusion_counts["analyst_exclusion"] == 1
    assert with_it.excluded_sample_count == 1
    assert "analyst_exclusion" not in without.exclusion_counts


async def test_revoked_exclusion_is_ignored(db_session) -> None:
    inst = await make_instance(db_session)
    source = await make_log_source(db_session, inst, qradar_id=1)
    buckets = await _series(db_session, source)
    db_session.add(
        _exclusion(
            source.id,
            buckets[0],
            revoked_by="bob",
            revoked_at=NOW,
            revocation_reason="mistake",
        )
    )
    await db_session.flush()
    await _rebuild(db_session, source)
    cell = await _cell(db_session, source.id)
    assert cell.sample_count == 12
    assert "analyst_exclusion" not in cell.exclusion_counts


async def test_a_cell_emptied_by_an_exclusion_is_unusable_and_the_verdict_fails_closed(
    db_session,
) -> None:
    inst = await make_instance(db_session)
    source = await make_log_source(db_session, inst, qradar_id=1)
    ten = await _series(db_session, source, hour=10)
    eleven = await _series(db_session, source, hour=11)
    await _rebuild(db_session, source)
    before = await _cell(db_session, source.id)
    assert before.is_reliable and before.sample_count == 12
    version_before = before.baseline_version

    # Every 10:00 sample goes to an analyst exclusion. Every 11:00 sample goes to an
    # automatic incident span instead.
    for bucket in ten:
        db_session.add(_exclusion(source.id, bucket))
    for bucket in eleven:
        db_session.add(
            LogSourceAnomaly(
                log_source_id=source.id,
                anomaly_type=AnomalyType.VOLUME_SPIKE,
                severity=Severity.HIGH,
                state=AnomalyState.RESOLVED,
                detected_at=bucket,
                anomaly_end=bucket + BUCKET,
                resolved_at=bucket + BUCKET,
                evidence_status=EvidenceStatus.NOT_REQUESTED,
                details={},
            )
        )
    await db_session.flush()
    await _rebuild(db_session, source)

    for metric in ("average_eps", "event_count"):
        cell = await _cell(db_session, source.id, metric=metric)
        assert cell.is_reliable is False
        assert cell.sample_count == 0
        assert cell.observations == []
        assert cell.p05 is None and cell.p95 is None
        assert cell.baseline_version == version_before + 1

    # The detector reads the cell as INSUFFICIENT_DATA, not NORMAL and not a narrower band.
    stored = await _cell(db_session, source.id)
    verdict = detect_volume_spike(_ctx(_point(eps=50.0), eps_cell=baseline_cell(stored)))
    assert verdict.signal is DetectorSignal.UNKNOWN  # the detector's INSUFFICIENT_DATA
    assert verdict.expected_value is None

    # A cell that lost its samples to the automatic span keeps today's behaviour: untouched.
    other = await _cell(db_session, source.id, hour=11)
    assert other.is_reliable and other.sample_count == 12


async def test_the_detector_still_judges_a_cell_that_kept_enough_samples(db_session) -> None:
    inst = await make_instance(db_session)
    source = await make_log_source(db_session, inst, qradar_id=1)
    buckets = await _series(db_session, source)
    db_session.add(_exclusion(source.id, buckets[0]))
    await db_session.flush()
    await _rebuild(db_session, source)
    cell = await _cell(db_session, source.id)
    assert cell.is_reliable and cell.sample_count == 11


async def test_automatic_and_analyst_spans_charge_each_bucket_once(db_session) -> None:
    inst = await make_instance(db_session)
    source = await make_log_source(db_session, inst, qradar_id=1)
    buckets = await _series(db_session, source)
    db_session.add(
        LogSourceAnomaly(
            log_source_id=source.id,
            anomaly_type=AnomalyType.VOLUME_SPIKE,
            severity=Severity.HIGH,
            state=AnomalyState.RESOLVED,
            detected_at=buckets[0],
            anomaly_end=buckets[0] + BUCKET,
            resolved_at=buckets[0] + BUCKET,
            evidence_status=EvidenceStatus.NOT_REQUESTED,
            details={},
        )
    )
    # The analyst spans cover the incident bucket and the next week's bucket.
    db_session.add(_exclusion(source.id, buckets[0]))
    db_session.add(_exclusion(source.id, buckets[1]))
    await db_session.flush()
    await _rebuild(db_session, source)
    cell = await _cell(db_session, source.id)
    # Chosen attribution: the automatic reason wins, so the analyst count is the
    # marginal effect. Each bucket appears once; the totals add up.
    assert cell.exclusion_counts["known_anomaly"] >= 1
    assert cell.exclusion_counts["analyst_exclusion"] == 1
    assert cell.excluded_sample_count == sum(cell.exclusion_counts.values())
    assert cell.sample_count == 12 - cell.excluded_sample_count


async def test_exclusions_are_read_once_whatever_the_number_of_sources(db_session) -> None:
    inst = await make_instance(db_session)
    sources = []
    for index in range(5):
        source = await make_log_source(db_session, inst, qradar_id=index + 1, name=f"s{index}")
        buckets = await _series(db_session, source)
        db_session.add(_exclusion(source.id, buckets[0]))
        sources.append(source)
    await db_session.flush()
    builder = BaselineBuilder(db_session, settings=_settings())

    reads = []
    for batch in (sources[:1], sources):
        with counting(db_session) as statements:
            await builder.rebuild_many(batch, now=NOW)
        reads.append(
            sum("baseline_training_exclusion" in statement for statement in statements)
        )
    assert reads == [1, 1]


async def test_the_cell_invalidation_is_one_statement_for_all_sources(db_session) -> None:
    inst = await make_instance(db_session)
    sources = []
    for index in range(4):
        source = await make_log_source(db_session, inst, qradar_id=index + 1, name=f"s{index}")
        buckets = await _series(db_session, source)
        sources.append((source, buckets))
    builder = BaselineBuilder(db_session, settings=_settings())
    await builder.rebuild_many([s for s, _ in sources], now=NOW)
    for source, buckets in sources:
        for bucket in buckets:
            db_session.add(_exclusion(source.id, bucket))
    await db_session.flush()
    with counting(db_session) as statements:
        await builder.rebuild_many([s for s, _ in sources], now=NOW)
    updates = [s for s in statements if s.lstrip().upper().startswith("UPDATE LOG_SOURCE_BASELINE")]
    assert len(updates) == 1
    for source, _ in sources:
        assert (await _cell(db_session, source.id)).is_reliable is False


async def test_rebuild_for_source_without_spans_issues_no_exclusion_read(db_session) -> None:
    inst = await make_instance(db_session)
    source = await make_log_source(db_session, inst, qradar_id=1)
    await _series(db_session, source)
    with counting(db_session) as statements:
        await BaselineBuilder(db_session, settings=_settings()).rebuild_for_source(source, now=NOW)
    assert not any("baseline_training_exclusion" in statement for statement in statements)


# --- the APPLIED / PENDING_REBUILD effect state over HTTP -----------------------------------

READER = Principal("rita", permissions=frozenset({"read:anomalies"}))


def test_state_is_pending_until_a_rebuild_newer_than_the_row(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(session):  # type: ignore[no-untyped-def]
        inst = await make_instance(session)
        source = await make_log_source(session, inst, qradar_id=1)
        await _series(session, source)
        row = _exclusion(source.id, RECENT)
        session.add(row)
        await session.flush()
        return source.id

    source_id = run(seed)
    url = "/api/v1/behavior/training-exclusions"

    def states() -> list[str]:
        items = Client(READER).get(url, params={"subject_id": str(source_id)}).json()["items"]
        return [item["state"] for item in items]

    assert states() == ["PENDING_REBUILD"]

    async def rebuild(session):  # type: ignore[no-untyped-def]
        source = await session.get(type(await _first_source(session)), source_id)
        await BaselineBuilder(session, settings=_settings()).rebuild_many(
            [source], now=datetime.now(UTC) + timedelta(minutes=1)
        )

    run(rebuild)
    assert states() == ["APPLIED"]
    client = Client(READER)
    assert len(client.get(url, params={"state": "APPLIED"}).json()["items"]) == 1
    assert client.get(url, params={"state": "PENDING_REBUILD"}).json()["items"] == []

    async def newer(session):  # type: ignore[no-untyped-def]
        session.add(_exclusion(
                source_id, RECENT - H, RECENT, created_at=datetime.now(UTC) + timedelta(minutes=5)
            ))

    run(newer)
    # Created after the last rebuild: pending again; the first row stays applied.
    assert sorted(states()) == ["APPLIED", "PENDING_REBUILD"]


async def _first_source(session):  # type: ignore[no-untyped-def]
    from app.models.log_source import LogSource

    return await session.scalar(select(LogSource).limit(1))


def test_the_nightly_task_honours_exclusions_end_to_end(db_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The operator's path: `rebuild_baselines` reads the exclusions and applies them."""
    import asyncio

    from app.workers import tasks
    from tests.integration.test_fleet_api import _maker

    settings = Settings(
        encryption_key="x" * 44, baseline_min_samples=8, baseline_lookback_days=900
    )
    monkeypatch.setattr("app.anomaly.baseline.get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: _maker()[1])

    outlier = [50.0, 52.0, 48.0, 51.0, 49.0, 50.0, 52.0, 48.0, 51.0, 49.0, 50.0, 900.0]

    async def seed(session):  # type: ignore[no-untyped-def]
        inst = await make_instance(session)
        ids = {}
        for name, qid in (("plain", 1), ("trimmed", 2), ("emptied", 3)):
            source = await make_log_source(
                session, inst, qradar_id=qid, name=name, monitoring_enabled=True
            )
            buckets = await _series(session, source, values=outlier)
            ids[name] = (source.id, buckets)
        return ids

    ids = run(seed)
    assert asyncio.run(tasks.rebuild_baselines())["sources"] == 3

    async def read(session):  # type: ignore[no-untyped-def]
        return {
            name: await _cell(session, source_id) for name, (source_id, _) in ids.items()
        }

    first = run(read)
    assert all(cell.is_reliable and cell.sample_count == 12 for cell in first.values())

    async def exclude(session):  # type: ignore[no-untyped-def]
        session.add(_exclusion(ids["trimmed"][0], ids["trimmed"][1][-1]))
        for bucket in ids["emptied"][1]:
            session.add(_exclusion(ids["emptied"][0], bucket))

    run(exclude)
    assert asyncio.run(tasks.rebuild_baselines())["sources"] == 3

    async def verdicts(session):  # type: ignore[no-untyped-def]
        cells = await read(session)
        stored = cells["emptied"]
        verdict = detect_volume_spike(_ctx(_point(eps=50.0), eps_cell=baseline_cell(stored)))
        return cells, verdict

    cells, verdict = run(verdicts)
    assert cells["plain"].sample_count == 12 and cells["plain"].is_reliable
    assert cells["trimmed"].sample_count == 11 and 900.0 not in cells["trimmed"].observations
    assert cells["trimmed"].p95 < cells["plain"].p95
    assert cells["emptied"].is_reliable is False and cells["emptied"].sample_count == 0
    assert verdict.signal is DetectorSignal.UNKNOWN


async def test_a_failing_source_propagates_as_the_per_source_loop_did(
    db_session, monkeypatch
) -> None:
    """The callers' loops never isolated failures: the run fails and its transaction rolls
    back. `rebuild_many` keeps that, and leaves the builder reusable."""
    inst = await make_instance(db_session)
    first = await make_log_source(db_session, inst, qradar_id=1, name="a")
    second = await make_log_source(db_session, inst, qradar_id=2, name="b")
    builder = BaselineBuilder(db_session, settings=_settings())
    original = BaselineBuilder._rebuild

    async def boom(self, source, now, spans):  # type: ignore[no-untyped-def]
        if source.id == second.id:
            raise RuntimeError("boom")
        return await original(self, source, now, spans)

    monkeypatch.setattr(BaselineBuilder, "_rebuild", boom)
    with pytest.raises(RuntimeError):
        await builder.rebuild_many([first, second], now=NOW)
    assert builder._stale_sink is None
