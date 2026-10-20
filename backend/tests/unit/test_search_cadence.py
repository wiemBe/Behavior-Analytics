"""Pure search-cadence arithmetic and frozen migration preset coverage."""

from __future__ import annotations

import re
import runpy
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.models.enums import SearchCadence
from app.models.search_cadence import SearchCadenceSetting
from app.services.search_cadence import SearchCadenceService, slot_start
from app.workers.celery_app import celery_app


@pytest.mark.parametrize("cadence", list(SearchCadence))
def test_slot_start_is_epoch_aligned_for_every_preset(cadence: SearchCadence) -> None:
    now = datetime(2026, 10, 12, 13, 47, 31, tzinfo=UTC)
    actual = slot_start(now, int(cadence), 0)
    assert int(actual.timestamp()) % int(cadence) == 0
    assert actual <= now < actual + timedelta(seconds=int(cadence))


def test_four_hour_slots_follow_the_utc_grid() -> None:
    assert slot_start(
        datetime(2026, 10, 12, 7, 59, 59, tzinfo=UTC), 14_400, 0
    ) == datetime(2026, 10, 12, 4, tzinfo=UTC)
    assert slot_start(
        datetime(2026, 10, 12, 8, 0, tzinfo=UTC), 14_400, 0
    ) == datetime(2026, 10, 12, 8, tzinfo=UTC)


def test_slot_start_holds_the_previous_slot_until_settle_boundary() -> None:
    boundary = datetime(2026, 10, 12, 12, tzinfo=UTC)
    assert slot_start(boundary + timedelta(seconds=119), 3600, 120) == boundary - timedelta(
        hours=1
    )
    assert slot_start(boundary + timedelta(seconds=120), 3600, 120) == boundary


def test_slot_start_rejects_a_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        slot_start(datetime(2026, 10, 12, 12), 3600, 120)


def test_next_dispatch_uses_current_slot_before_first_claim() -> None:
    settings = Settings(search_cadence_settle_seconds=120)
    service = SearchCadenceService(None, settings)  # type: ignore[arg-type]
    now = datetime(2026, 10, 12, 12, 3, tzinfo=UTC)
    row = SearchCadenceSetting(
        family="ANOMALY_EXPLANATION",
        cadence_seconds=3600,
        updated_at=now,
    )
    assert service.next_dispatch_at(row, now) == datetime(2026, 10, 12, 12, 2, tzinfo=UTC)


def test_next_dispatch_realigns_one_hour_claim_to_four_hour_grid() -> None:
    settings = Settings(search_cadence_settle_seconds=120)
    service = SearchCadenceService(None, settings)  # type: ignore[arg-type]
    now = datetime(2026, 10, 12, 14, 30, tzinfo=UTC)
    row = SearchCadenceSetting(
        family="ANOMALY_EXPLANATION",
        cadence_seconds=SearchCadence.FOUR_HOURS,
        last_slot_start=datetime(2026, 10, 12, 14, tzinfo=UTC),
        updated_at=now,
    )
    assert service.next_dispatch_at(row, now) == datetime(2026, 10, 12, 16, 2, tzinfo=UTC)


def test_next_dispatch_uses_current_unclaimed_slot_after_shortening_cadence() -> None:
    settings = Settings(search_cadence_settle_seconds=120)
    service = SearchCadenceService(None, settings)  # type: ignore[arg-type]
    now = datetime(2026, 10, 12, 14, 30, tzinfo=UTC)
    row = SearchCadenceSetting(
        family="ANOMALY_EXPLANATION",
        cadence_seconds=SearchCadence.ONE_HOUR,
        last_slot_start=datetime(2026, 10, 12, 12, tzinfo=UTC),
        updated_at=now,
    )
    assert service.next_dispatch_at(row, now) == datetime(2026, 10, 12, 14, 2, tzinfo=UTC)


def test_next_dispatch_same_grid_advances_by_one_cadence() -> None:
    settings = Settings(search_cadence_settle_seconds=120)
    service = SearchCadenceService(None, settings)  # type: ignore[arg-type]
    now = datetime(2026, 10, 12, 12, 3, tzinfo=UTC)
    row = SearchCadenceSetting(
        family="ANOMALY_EXPLANATION",
        cadence_seconds=SearchCadence.ONE_HOUR,
        last_slot_start=datetime(2026, 10, 12, 12, tzinfo=UTC),
        updated_at=now,
    )
    assert service.next_dispatch_at(row, now) == datetime(2026, 10, 12, 13, 2, tzinfo=UTC)


@pytest.mark.parametrize("old_cadence", list(SearchCadence))
@pytest.mark.parametrize("new_cadence", list(SearchCadence))
def test_next_dispatch_matches_the_first_slot_claim_would_accept(
    old_cadence: SearchCadence, new_cadence: SearchCadence
) -> None:
    settle = 120
    settings = Settings(search_cadence_settle_seconds=settle)
    service = SearchCadenceService(None, settings)  # type: ignore[arg-type]
    reference = datetime(2026, 10, 12, 14, 37, tzinfo=UTC)
    claimed = slot_start(reference, int(old_cadence), 0)
    row = SearchCadenceSetting(
        family="ANOMALY_EXPLANATION",
        cadence_seconds=int(new_cadence),
        last_slot_start=claimed,
        updated_at=reference,
    )

    for now in (
        claimed + timedelta(minutes=30),
        claimed + timedelta(hours=2, minutes=30),
        claimed + timedelta(hours=9, minutes=30),
    ):
        accepted = slot_start(now, int(new_cadence), settle)
        while accepted <= claimed:
            accepted += timedelta(seconds=int(new_cadence))
        assert service.next_dispatch_at(row, now) - timedelta(seconds=settle) == accepted


def test_migration_cadence_literal_matches_the_enum() -> None:
    migration = runpy.run_path("alembic/versions/0013_search_cadence.py")
    literal = migration["SEARCH_CADENCE_CHECK"]
    assert {int(value) for value in re.findall(r"\d+", literal)} == {
        int(cadence) for cadence in SearchCadence
    }


def test_settle_setting_is_bounded_and_explanation_beat_ticks_every_minute() -> None:
    assert Settings().search_cadence_settle_seconds == 120
    assert Settings(search_cadence_settle_seconds=0).search_cadence_settle_seconds == 0
    assert Settings(search_cadence_settle_seconds=900).search_cadence_settle_seconds == 900
    with pytest.raises(ValueError):
        Settings(search_cadence_settle_seconds=-1)
    with pytest.raises(ValueError):
        Settings(search_cadence_settle_seconds=901)
    assert celery_app.conf.beat_schedule["collect-anomaly-explanations"]["schedule"] == 60.0


def test_bucket_seconds_is_none_only_for_a_family_with_no_buckets() -> None:
    from app.core.config import get_settings
    from app.models.enums import SearchFamily
    from app.services.search_cadence import bucket_seconds

    settings = get_settings().model_copy(
        update={"collection_interval_seconds": 900, "rule_match_window_seconds": 3600}
    )
    assert bucket_seconds(SearchFamily.ANOMALY_EXPLANATION, settings) is None
    assert bucket_seconds(SearchFamily.PROPERTY_WATCH, settings) == 900
    assert bucket_seconds(SearchFamily.RULE_MATCH, settings) == 3600
    for family in SearchFamily:
        if family is not SearchFamily.ANOMALY_EXPLANATION:
            assert bucket_seconds(family, settings) is not None
