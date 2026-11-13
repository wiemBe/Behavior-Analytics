"""SOC-13h: rule storm and silence honour analyst training exclusions.

Storm adds ``NOT EXISTS baseline_training_exclusion`` to the bulk cell SQL, in
both the batch statement and the section statement. Silence filters the
in-memory grid with one read per run. The two scopes stay apart: a storm
hold-out must not blank a silence baseline.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.anomaly import training_exclusions as te
from app.models.enums import AnomalyState, RuleAnomalyType, Severity
from app.models.rule import AnalyticsRule, RuleMatchWindow
from app.models.rule_anomaly import RuleAnomaly
from app.rules.anomalies import slot_of
from app.rules.engine import RuleMatchEngine
from app.security.rbac import Principal
from tests.integration.factories import make_instance
from tests.integration.test_fleet_api import Client, run
from tests.integration.test_rule_match_collection import recorded_statements
from tests.integration.test_rule_match_engine import (
    WIDTH,
    _engine,
    _metric,
    _rule,
    _silence_fixture,
    _watermark,
    _window,
)

pytestmark = [pytest.mark.integration]

TRIAGER = Principal("ada", permissions=frozenset({"read:anomalies", "anomaly:triage"}))
READER = Principal("rita", permissions=frozenset({"read:*"}))
WEEK = timedelta(days=7)


async def exclude(session, rule_id, start, end, *, scope: str):  # type: ignore[no-untyped-def]
    result = await te.create(
        session,
        origin_family="rule",
        origin_anomaly_id=uuid.uuid4(),
        subject_kind="rule",
        subject_id=rule_id,
        scope_key=scope,
        starts_at=start,
        ends_at=end,
        reason="analyst removed this window",
        created_by="ada",
        lookback_start=start - timedelta(days=30),
    )
    return result.row


def _silence_reads(statements: list[str]) -> int:
    return sum(
        1
        for sql in statements
        if "baseline_training_exclusion" in sql
        and "-- rule-engine storm" not in sql
        and "-- rule-section storm" not in sql
    )


def _storm_sql(statements: list[str], marker: str) -> list[str]:
    return [sql for sql in statements if marker in sql]


async def _plant(session, *, values: list[int]):  # type: ignore[no-untyped-def]
    """Weekly samples of one slot, then a judged window that is not in the cell."""
    instance = await make_instance(session)
    first = datetime(2026, 6, 2, 10, tzinfo=UTC)
    born = first - timedelta(days=40)
    rule = await _rule(session, instance, qradar_id=11, name="weekly", born=born)
    starts = [first + index * WEEK for index in range(len(values))]
    judged = starts[-1]
    session.add_all(_window(instance, start) for start in starts)
    session.add_all(
        _metric(rule, start, count) for start, count in zip(starts, values, strict=True)
    )
    clock = judged + timedelta(seconds=WIDTH)
    await _watermark(session, instance, clock)
    await session.flush()
    engine = _engine(
        session,
        clock,
        rule_storm_min_samples=1,
        rule_storm_min_visits=1,
        rule_storm_min_window_events=1,
    )
    return instance, rule, judged, engine


async def _cell(engine: RuleMatchEngine, instance_id, rule_id, judged):  # type: ignore[no-untyped-def]
    lookback = judged - timedelta(days=engine.settings.rule_storm_lookback_days)
    cells = await engine._load_cells(
        instance_id,
        lookback,
        judged,
        judged + timedelta(seconds=WIDTH),
        WIDTH,
        [judged],
        engine._thresholds(),
    )
    key = slot_of(judged, WIDTH)
    assert key is not None
    return cells.get((rule_id, key[0], key[1]))


async def test_excluded_interval_changes_the_storm_band(db_session) -> None:
    instance, rule, judged, engine = await _plant(db_session, values=[10, 900, 30])
    outlier = judged - WEEK
    before = await _cell(engine, instance.id, rule.id, judged)
    assert before is not None
    assert before.summary.median == pytest.approx(455)
    assert before.summary.sample_count == 2

    await exclude(
        db_session, rule.id, outlier, outlier + timedelta(seconds=WIDTH),
        scope=te.RULE_STORM_SCOPE,
    )
    with recorded_statements(db_session) as statements:
        cell = await _cell(engine, instance.id, rule.id, judged)
        section = await engine.read_rule(rule.id)
    assert cell is not None
    assert cell.summary.median == pytest.approx(10)
    assert cell.summary.sample_count == 1
    assert section is not None and section.storm is not None
    assert section.storm.expected_value == pytest.approx(10)
    batch = _storm_sql(statements, "-- rule-engine storm")
    section_sql = _storm_sql(statements, "-- rule-section storm")
    assert len(batch) == 1 and len(section_sql) == 1
    for sql in (*batch, *section_sql):
        assert "baseline_training_exclusion" in sql
        assert "ex.scope_key = 'rule:storm'" in sql
        assert "ex.revoked_at IS NULL" in sql


async def test_a_window_at_ends_at_stays_in_the_storm_cell(db_session) -> None:
    instance, rule, judged, engine = await _plant(db_session, values=[10, 900, 30])
    early = judged - 2 * WEEK
    kept = judged - WEEK
    # Half-open: the window that starts at ends_at is the 900, and it stays.
    await exclude(db_session, rule.id, early, kept, scope=te.RULE_STORM_SCOPE)
    cell = await _cell(engine, instance.id, rule.id, judged)
    assert cell is not None
    assert cell.summary.sample_count == 1
    assert cell.summary.median == pytest.approx(900)


async def test_revoked_storm_exclusion_trains_the_window_again(db_session) -> None:
    instance, rule, judged, engine = await _plant(db_session, values=[10, 900, 30])
    outlier = judged - WEEK
    row = await exclude(
        db_session, rule.id, outlier, outlier + timedelta(seconds=WIDTH),
        scope=te.RULE_STORM_SCOPE,
    )
    held = await _cell(engine, instance.id, rule.id, judged)
    assert held is not None and held.summary.median == pytest.approx(10)
    await te.revoke(db_session, row.id, revoked_by="ada", reason="put the window back")
    restored = await _cell(engine, instance.id, rule.id, judged)
    section = await engine.read_rule(rule.id)
    assert restored is not None and restored.summary.median == pytest.approx(455)
    assert section is not None and section.storm is not None
    assert section.storm.expected_value == pytest.approx(455)


async def test_a_fully_excluded_storm_cell_is_insufficient(db_session) -> None:
    instance, rule, judged, engine = await _plant(db_session, values=[10, 900, 30])
    for start in (judged - 2 * WEEK, judged - WEEK):
        await exclude(
            db_session, rule.id, start, start + timedelta(seconds=WIDTH),
            scope=te.RULE_STORM_SCOPE,
        )
    cell = await _cell(engine, instance.id, rule.id, judged)
    section = await engine.read_rule(rule.id)
    assert cell is None
    assert section is not None and section.storm is not None
    assert section.storm.insufficient_data is True
    assert section.storm.expected_value is None
    assert section.storm.extra.get("skip") == "cell_missing"


async def test_automatic_and_analyst_overlap_is_counted_once(db_session) -> None:
    instance, rule, judged, engine = await _plant(db_session, values=[10, 40, 70, 30])
    both = judged - 3 * WEEK
    analyst_only = judged - 2 * WEEK
    db_session.add(
        RuleAnomaly(
            rule_id=rule.id,
            qradar_id=rule.qradar_id,
            detector_type=RuleAnomalyType.RULE_STORM.value,
            fingerprint="storm-overlap",
            severity=Severity.HIGH.value,
            state=AnomalyState.RESOLVED.value,
            detected_at=both,
            anomaly_end=both,
            resolved_at=both,
        )
    )
    await db_session.flush()
    await exclude(
        db_session, rule.id, both, both + timedelta(seconds=WIDTH), scope=te.RULE_STORM_SCOPE
    )
    await exclude(
        db_session, rule.id, analyst_only, analyst_only + timedelta(seconds=WIDTH),
        scope=te.RULE_STORM_SCOPE,
    )
    cell = await _cell(engine, instance.id, rule.id, judged)
    assert cell is not None
    assert cell.summary.sample_count == 1
    assert cell.summary.median == pytest.approx(70)


async def test_a_silence_exclusion_does_not_change_the_storm_cell(db_session) -> None:
    instance, rule, judged, engine = await _plant(db_session, values=[10, 900, 30])
    outlier = judged - WEEK
    await exclude(
        db_session, rule.id, outlier, outlier + timedelta(seconds=WIDTH),
        scope=te.RULE_SILENCE_SCOPE,
    )
    cell = await _cell(engine, instance.id, rule.id, judged)
    assert cell is not None
    assert cell.summary.median == pytest.approx(455)
    assert cell.summary.sample_count == 2


async def test_a_held_out_baseline_window_fails_silence_closed(db_session) -> None:
    instance, rule, starts, clock = await _silence_fixture(db_session)
    held = starts[0] - timedelta(hours=1)
    await exclude(
        db_session, rule.id, held, held + timedelta(seconds=WIDTH), scope=te.RULE_SILENCE_SCOPE
    )
    engine = _engine(db_session, clock)
    rule_id = rule.id
    report = await engine.evaluate(instance, window_starts=starts)
    # evaluate expires the session, so the rule id is read before that.
    section = await engine.read_rule(rule_id)
    assert report.incidents_opened == 0
    assert await db_session.scalar(select(RuleAnomaly)) is None
    assert section is not None and section.silence is not None
    assert section.silence.insufficient_data is True
    assert section.silence.extra.get("skip") == "baseline_incomplete"
    assert section.silence.extra["baseline_tally"]["missing"] >= 1


async def test_a_storm_exclusion_does_not_stop_silence(db_session) -> None:
    instance, rule, starts, clock = await _silence_fixture(db_session)
    held = starts[0] - timedelta(hours=1)
    await exclude(
        db_session, rule.id, held, held + timedelta(seconds=WIDTH), scope=te.RULE_STORM_SCOPE
    )
    report = await _engine(db_session, clock).evaluate(instance, window_starts=starts)
    assert report.incidents_opened == 1


async def test_silence_exclusions_are_read_once_per_run(db_session) -> None:
    base = datetime(2026, 6, 8, 0, tzinfo=UTC)
    starts = [base + timedelta(hours=hour) for hour in range(2)]
    clock = base + timedelta(hours=4)

    async def judge(count: int) -> list[str]:
        instance = await make_instance(db_session)
        for index in range(count):
            await _rule(
                db_session, instance, qradar_id=index + 1, name=f"rule-{index}", rule_type="COMMON"
            )
        db_session.add_all(_window(instance, start) for start in starts)
        await db_session.flush()
        engine = _engine(db_session, clock)
        with recorded_statements(db_session) as statements:
            report = await engine.evaluate(instance, window_starts=starts)
        assert report.rules_seen == count
        return statements

    one = await judge(1)
    many = await judge(4)
    assert _silence_reads(one) == 1
    assert _silence_reads(many) == 1
    assert sum("baseline_training_exclusion" in sql for sql in many) == 2

    bare = await make_instance(db_session)
    db_session.add_all(_window(bare, start) for start in starts)
    await db_session.flush()
    engine = _engine(db_session, clock)
    with recorded_statements(db_session) as statements:
        await engine.evaluate(bare, window_starts=starts)
    assert _silence_reads(statements) == 0
    assert sum("baseline_training_exclusion" in sql for sql in statements) == 1

    off = _engine(db_session, clock, rule_match_collection_enabled=False)
    with recorded_statements(db_session) as statements:
        report = await off.evaluate(bare, window_starts=starts)
    assert report.skipped == "collection_disabled"
    assert _silence_reads(statements) == 0
    assert not any("baseline_training_exclusion" in sql for sql in statements)


# --- API ---------------------------------------------------------------------------


def _anchor() -> datetime:
    return (datetime.now(UTC) - timedelta(days=2)).replace(minute=0, second=0, microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _body(start: datetime, end: datetime, reason: str = "change window") -> dict:
    return {"starts_at": _iso(start), "ends_at": _iso(end), "reason": reason}


def _url(anomaly_id: uuid.UUID, family: str = "rule") -> str:
    return f"/api/v1/anomalies/{family}/{anomaly_id}/training-exclusions"


def _code(response) -> str:  # type: ignore[no-untyped-def]
    body = response.json()
    detail = body.get("detail")
    if isinstance(detail, dict) and "code" in detail:
        return str(detail["code"])
    return str(body["detail_code"])


def _match_window(instance_id, at, *, seconds: int, origin: str) -> RuleMatchWindow:
    return RuleMatchWindow(
        instance_id=instance_id,
        window_start=at,
        window_seconds=seconds,
        origin=origin,
        status="COMPLETE",
        rows_returned=0,
        row_cap=5000,
        unknown_ids=0,
        query_version="v1",
        collected_at=at,
        attempts=1,
    )


async def _seed_rule(  # type: ignore[no-untyped-def]
    session,
    detector: RuleAnomalyType,
    *,
    state: AnomalyState = AnomalyState.RESOLVED,
    windows: str = "live",
) -> tuple[uuid.UUID, uuid.UUID]:
    instance = await make_instance(session)
    detected = _anchor()
    rule = AnalyticsRule(
        instance_id=instance.id,
        qradar_id=21,
        name="seeded-rule",
        enabled=True,
        first_seen_at=detected - timedelta(days=30),
    )
    session.add(rule)
    await session.flush()
    if windows == "live":
        session.add(_match_window(instance.id, detected, seconds=3600, origin="LIVE"))
    elif windows == "history":
        session.add(_match_window(instance.id, detected, seconds=300, origin="HISTORY"))
    elif windows == "both":
        session.add(
            _match_window(
                instance.id, detected - timedelta(seconds=300), seconds=300, origin="LIVE"
            )
        )
        session.add(_match_window(instance.id, detected, seconds=3600, origin="HISTORY"))
    anomaly = RuleAnomaly(
        rule_id=rule.id,
        qradar_id=rule.qradar_id,
        detector_type=detector.value,
        fingerprint=f"rule-{uuid.uuid4().hex[:12]}",
        severity=Severity.MEDIUM.value,
        state=state.value,
        detected_at=detected,
        resolved_at=(
            None if state is not AnomalyState.RESOLVED else detected + timedelta(seconds=300)
        ),
    )
    session.add(anomaly)
    await session.flush()
    return anomaly.id, rule.id


def seed_rule(detector: RuleAnomalyType, **kw):  # type: ignore[no-untyped-def]
    async def work(session):  # type: ignore[no-untyped-def]
        return await _seed_rule(session, detector, **kw)

    return run(work)


@pytest.mark.parametrize(
    ("detector", "scope"),
    [
        (RuleAnomalyType.RULE_STORM, "rule:storm"),
        (RuleAnomalyType.RULE_SILENCE, "rule:silence"),
    ],
)
def test_api_accepts_a_resolved_rule_anomaly(
    db_schema, detector: RuleAnomalyType, scope: str
) -> None:
    anomaly_id, rule_id = seed_rule(detector, windows="both")
    start = _anchor()
    # The live row is 300 seconds. An hour interval is not that width's boundary
    # on the end, so the accepted interval is one live bucket.
    response = Client(TRIAGER).post(
        _url(anomaly_id), json=_body(start, start + timedelta(seconds=300))
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["origin_family"] == "rule"
    assert body["subject_kind"] == "rule"
    assert body["subject_id"] == str(rule_id)
    assert body["scope_key"] == scope
    assert body["state"] == "PENDING_REBUILD"
    again = Client(TRIAGER).post(
        _url(anomaly_id), json=_body(start, start + timedelta(seconds=300))
    )
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]


def test_storm_and_silence_are_different_scopes(db_schema) -> None:  # type: ignore[no-untyped-def]
    start = _anchor()
    storm_id, rule_id = seed_rule(RuleAnomalyType.RULE_STORM)
    silence_id, _other = seed_rule(RuleAnomalyType.RULE_SILENCE)

    async def share(session):  # type: ignore[no-untyped-def]
        storm = await session.get(RuleAnomaly, storm_id)
        silence = await session.get(RuleAnomaly, silence_id)
        assert storm is not None and silence is not None
        silence.rule_id = storm.rule_id

    # Two anomalies, one rule: the scopes do not overlap each other.
    run(share)
    hour = timedelta(hours=1)
    storm = Client(TRIAGER).post(_url(storm_id), json=_body(start, start + hour))
    silence = Client(TRIAGER).post(_url(silence_id), json=_body(start, start + hour))
    assert storm.status_code == 201, storm.text
    assert silence.status_code == 201, silence.text
    assert storm.json()["scope_key"] == "rule:storm"
    assert silence.json()["scope_key"] == "rule:silence"
    assert storm.json()["subject_id"] == silence.json()["subject_id"] == str(rule_id)


def test_rule_guards_match_the_other_families(db_schema) -> None:  # type: ignore[no-untyped-def]
    start = _anchor()
    step = timedelta(hours=1)
    open_id, _ = seed_rule(RuleAnomalyType.RULE_STORM, state=AnomalyState.OPEN)
    assert _code(Client(TRIAGER).post(_url(open_id), json=_body(start, start + step))) == (
        "baseline_exclusion.anomaly_active"
    )
    missing = Client(TRIAGER).post(_url(uuid.uuid4()), json=_body(start, start + step))
    assert missing.status_code == 404
    assert _code(missing) == "baseline_exclusion.not_found"

    history_only, _ = seed_rule(RuleAnomalyType.RULE_SILENCE, windows="history")
    assert _code(Client(TRIAGER).post(_url(history_only), json=_body(start, start + step))) == (
        "baseline_exclusion.interval_invalid"
    )
    no_window, _ = seed_rule(RuleAnomalyType.RULE_STORM, windows="none")
    assert _code(Client(TRIAGER).post(_url(no_window), json=_body(start, start + step))) == (
        "baseline_exclusion.interval_invalid"
    )

    anomaly_id, rule_id = seed_rule(RuleAnomalyType.RULE_STORM)
    refused = Client(READER).post(_url(anomaly_id), json=_body(start, start + step))
    assert refused.status_code == 403
    blank = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + step, reason="  "))
    assert _code(blank) == "baseline_exclusion.reason_invalid"

    narrowed = Client(TRIAGER).post(_url(anomaly_id), json=_body(start + step, start + 2 * step))
    assert _code(narrowed) == "baseline_exclusion.interval_not_widened"
    too_long = Client(TRIAGER).post(
        _url(anomaly_id), json=_body(start, start + timedelta(days=7) + step)
    )
    assert _code(too_long) == "baseline_exclusion.interval_too_long"
    created = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + step))
    assert created.status_code == 201, created.text
    overlap = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + 2 * step))
    assert overlap.status_code == 409
    assert _code(overlap) == "baseline_exclusion.overlap"
    assert created.json()["state"] == "PENDING_REBUILD"

    def listed(state: str) -> list[dict]:
        response = Client(TRIAGER).get(
            "/api/v1/behavior/training-exclusions",
            params={"subject_kind": "rule", "subject_id": str(rule_id), "state": state},
        )
        assert response.status_code == 200, response.text
        return response.json()["items"]

    assert [item["id"] for item in listed("PENDING_REBUILD")] == [created.json()["id"]]
    assert listed("APPLIED") == []


def test_bucket_width_comes_from_the_live_row(db_schema) -> None:  # type: ignore[no-untyped-def]
    start = _anchor()
    history_only, _ = seed_rule(RuleAnomalyType.RULE_STORM, windows="history")
    asked = _body(start, start + timedelta(seconds=300))
    assert _code(Client(TRIAGER).post(_url(history_only), json=asked)) == (
        "baseline_exclusion.interval_invalid"
    )
    anomaly_id, _ = seed_rule(RuleAnomalyType.RULE_SILENCE, windows="both")
    accepted = Client(TRIAGER).post(_url(anomaly_id), json=asked)
    assert accepted.status_code == 201, accepted.text
