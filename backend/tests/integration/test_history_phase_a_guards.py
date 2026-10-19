"""Phase A history, phase 1: the readers that must never see a history row (HB-3, §B.4.1).

A history row is `collection_source = 'ariel_history'` (or `mock_history`). These tests
insert such rows directly, before any history writer exists, and pin that the detectors'
pending reads, the first-run read, the NO_EVENTS streak and the F.1 loaders all ignore them,
while the preview, which replays and persists nothing, reads them.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.anomaly.engine import AnomalyEngine
from app.core.config import Settings, get_settings
from app.custom_rules.engine import CustomRuleEngine
from app.custom_rules.loaders import load_fleet_classes, load_log_sources
from app.custom_rules.preview import replay
from app.models.custom_rule import CustomRule, CustomRuleBreach
from app.models.enums import (
    ClassificationReviewStatus,
    CustomRuleMetric,
    CustomRuleSubjectKind,
    Severity,
    SourceClass,
)
from app.models.fleet import LogSourceClassification
from app.models.monitoring import AnomalyEvaluationWatermark
from tests.integration.factories import add_metric, make_instance, make_log_source, utc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

HISTORY = "ariel_history"
MOCK_HISTORY = "mock_history"


def _settings(**overrides: object) -> Settings:
    values = get_settings().model_dump()
    values.update(
        custom_rules_enabled=True,
        collection_interval_seconds=300,
        custom_rules_max_buckets_per_run=288,
    )
    values.update(overrides)
    return Settings(**values)


async def _source(session):  # type: ignore[no-untyped-def]
    instance = await make_instance(session)
    return await make_log_source(session, instance, qradar_id=41, name="edge-fw")


async def _history(session, source, bucket_start, **kw):  # type: ignore[no-untyped-def]
    kw.setdefault("collection_source", HISTORY)
    return await add_metric(session, source, bucket_start, **kw)


# ------------------------------------------------------------------ guards 2 and 3


async def test_a_history_row_after_the_watermark_is_never_pending(db_session) -> None:
    """Guard 3: the pending read takes live rows only, also when the watermark is right."""
    source = await _source(db_session)
    await add_metric(db_session, source, utc(h=10, mi=0))
    await _history(db_session, source, utc(h=10, mi=5))
    await _history(db_session, source, utc(h=10, mi=10), collection_source=MOCK_HISTORY)
    db_session.add(
        AnomalyEvaluationWatermark(
            log_source_id=source.id, evaluated_through=utc(h=10, mi=0), updated_at=utc(h=10, mi=1)
        )
    )
    await db_session.flush()

    report = await AnomalyEngine(db_session, settings=_settings()).evaluate_pending(source)

    assert report.skipped_reason == "no pending metrics"
    assert report.interval_start is None
    mark = await db_session.get(AnomalyEvaluationWatermark, source.id)
    assert mark is not None and mark.evaluated_through == utc(h=10, mi=0)


async def test_a_history_row_alone_is_never_evaluated_without_a_watermark(db_session) -> None:
    """Guard 2: no watermark and only history rows means there is nothing to evaluate."""
    source = await _source(db_session)
    await _history(db_session, source, utc(h=10, mi=0))
    await _history(db_session, source, utc(h=10, mi=5), collection_source=MOCK_HISTORY)

    report = await AnomalyEngine(db_session, settings=_settings()).evaluate_pending(source)

    assert report.skipped_reason == "no metrics"
    assert await db_session.get(AnomalyEvaluationWatermark, source.id) is None


async def test_the_first_run_takes_the_latest_live_row_even_when_history_is_newer(
    db_session,
) -> None:
    """Guard 2: a history row is newer on purpose; the first-run read still takes live."""
    source = await _source(db_session)
    await add_metric(db_session, source, utc(h=10, mi=0))
    await _history(db_session, source, utc(h=10, mi=20))

    report = await AnomalyEngine(db_session, settings=_settings()).evaluate_pending(source)

    assert report.interval_start == utc(h=10, mi=0)
    mark = await db_session.get(AnomalyEvaluationWatermark, source.id)
    assert mark is not None and mark.evaluated_through == utc(h=10, mi=0)


async def test_a_history_zero_never_extends_a_no_events_streak(db_session) -> None:
    """§B.4.1 "Streaks": the count of preceding empty buckets is live rows only."""
    source = await _source(db_session)
    zero = dict(event_count=0, average_eps=0.0, peak_eps=0.0, stored_event_count=0)
    await add_metric(db_session, source, utc(h=10, mi=0), **zero)
    await _history(db_session, source, utc(h=10, mi=5), **zero)
    await _history(db_session, source, utc(h=10, mi=10), collection_source=MOCK_HISTORY, **zero)
    judged = await add_metric(db_session, source, utc(h=10, mi=15), **zero)

    streak = await AnomalyEngine(db_session, settings=_settings())._preceding_empty_buckets(
        source.id, judged
    )

    assert streak == 1  # the live 10:00 zero only; the two history zeros do not count


# --------------------------------------------------------------------------- F.1


async def test_the_f1_loaders_skip_history_unless_asked(db_session) -> None:
    source = await _source(db_session)
    await add_metric(db_session, source, utc(h=10, mi=0), event_count=10)
    await _history(db_session, source, utc(h=10, mi=5), event_count=20)
    await _history(db_session, source, utc(h=10, mi=10), collection_source=MOCK_HISTORY)
    after, through = utc(h=9, mi=55), utc(h=10, mi=15)

    live = await load_log_sources(db_session, [source.id], after=after, through=through)
    both = await load_log_sources(
        db_session, [source.id], after=after, through=through, include_history=True
    )

    assert sorted(live[str(source.id)]) == [utc(h=10, mi=0)]
    assert sorted(both[str(source.id)]) == [utc(h=10, mi=0), utc(h=10, mi=5), utc(h=10, mi=10)]


async def test_the_fleet_class_loader_skips_history_unless_asked(db_session) -> None:
    source = await _source(db_session)
    db_session.add(
        LogSourceClassification(
            log_source_id=source.id,
            source_class=SourceClass.LINUX.value,
            basis="NAME_REGEX",
            conflicts=[],
            classified_at=utc(h=9),
            review_status=ClassificationReviewStatus.REVIEWED.value,
        )
    )
    await add_metric(db_session, source, utc(h=10, mi=0), event_count=10)
    await _history(db_session, source, utc(h=10, mi=5), event_count=20)
    after, through = utc(h=9, mi=55), utc(h=10, mi=15)
    key = SourceClass.LINUX.value

    live = await load_fleet_classes(db_session, [key], after=after, through=through)
    both = await load_fleet_classes(
        db_session, [key], after=after, through=through, include_history=True
    )

    assert sorted(live[key]) == [utc(h=10, mi=0)]
    assert sorted(both[key]) == [utc(h=10, mi=0), utc(h=10, mi=5)]


async def test_f1_never_breaches_on_a_history_row_after_the_kind_watermark(db_session) -> None:
    """A kind that already has a watermark reads everything after it: history must not count."""
    source = await _source(db_session)
    await add_metric(db_session, source, utc(h=9, mi=55), event_count=1, average_eps=1.0)
    rule = CustomRule(
        name="sustained-volume",
        subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
        log_source_id=source.id,
        metric=CustomRuleMetric.EVENT_COUNT.value,
        operator="GT",
        threshold_value=100.0,
        sustain_buckets=3,
        recovery_buckets=2,
        severity=Severity.HIGH,
        enabled=True,
        consecutive_breaching=0,
        consecutive_under=0,
    )
    db_session.add(rule)
    await db_session.flush()
    settings = _settings()
    seeded = await CustomRuleEngine(db_session, settings, clock=lambda: utc(h=10, mi=0)).run()
    assert seeded.status == "seeded"  # the watermark now lies before the history rows

    for minute in (0, 5, 10, 15):
        await _history(
            db_session, source, utc(h=10, mi=minute), event_count=400, average_eps=1.0
        )
    result = await CustomRuleEngine(db_session, settings, clock=lambda: utc(h=10, mi=21)).run()

    assert result.opened == 0
    assert result.judged_buckets == 0
    assert await db_session.scalar(select(func.count()).select_from(CustomRuleBreach)) == 0


async def test_the_preview_reads_history_rows(db_session) -> None:
    source = await _source(db_session)
    for minute in (0, 5, 10):
        await _history(
            db_session, source, utc(h=10, mi=minute), event_count=400, average_eps=1.0
        )
    now = utc(h=10, mi=16)

    result = await replay(
        db_session,
        metric=CustomRuleMetric.EVENT_COUNT,
        operator="GT",
        threshold_value=100.0,
        sustain_buckets=3,
        recovery_buckets=1,
        severity=Severity.HIGH.value,
        subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
        log_source_id=source.id,
        source_class=None,
        waf_endpoint_id=None,
        ips_entity_id=None,
        hours=1,
        interval_seconds=300,
        now=now,
    )

    measured = [bucket for bucket in result.buckets if bucket.value == 400.0]
    assert len(measured) == 3
    assert result.breach_intervals, "the replay judges the history rows it reads"


async def test_evaluate_latest_takes_the_latest_live_row_not_a_newer_history_row(
    db_session,
) -> None:
    """`evaluate_latest` feeds a detector too (no caller today); it takes live rows only."""
    source = await _source(db_session)
    await add_metric(db_session, source, utc(h=10, mi=0))
    await _history(db_session, source, utc(h=10, mi=20))

    report = await AnomalyEngine(db_session, settings=_settings()).evaluate_latest(source)

    assert report.interval_start == utc(h=10, mi=0)
