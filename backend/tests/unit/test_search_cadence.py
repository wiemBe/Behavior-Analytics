"""Pure search-cadence arithmetic and frozen migration preset coverage."""

from __future__ import annotations

import re
import runpy
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.i18n.messages import render
from app.models.enums import SearchCadence
from app.models.search_cadence import SearchCadenceSetting
from app.services.search_cadence import SearchCadenceService, family_enabled, slot_start
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


def test_a_failed_run_waits_for_the_next_five_minute_slot() -> None:
    # A failed run keeps the slot it claimed (19:20 here), so the family is not
    # retried early: the next run is the next slot, 19:25, plus the settle margin.
    settings = Settings(search_cadence_settle_seconds=120)
    service = SearchCadenceService(None, settings)  # type: ignore[arg-type]
    now = datetime(2026, 11, 4, 19, 26, tzinfo=UTC)
    row = SearchCadenceSetting(
        family="CATCHALL_SENDERS",
        cadence_seconds=SearchCadence.FIVE_MINUTES,
        last_slot_start=datetime(2026, 11, 4, 19, 20, tzinfo=UTC),
        last_run_at=datetime(2026, 11, 4, 19, 24, tzinfo=UTC),
        last_run_outcome="failed",
        updated_at=now,
    )
    assert service.next_dispatch_at(row, now) == datetime(2026, 11, 4, 19, 27, tzinfo=UTC)


def test_a_failed_run_at_one_hour_reproduces_the_ops_30_33_34_35_gap() -> None:
    # OPS-30/33/34/35 read "last run 12:20 Failed, next run 13:02". That is a
    # run at 12:20 claiming the 12:00 slot of a one-hour cadence (floor of
    # 12:20 - 120 s), plus one cadence and the 120 s settle margin. It cannot be
    # a five-minute cadence: the next slot after 12:20 would be 12:27.
    settings = Settings(search_cadence_settle_seconds=120)
    service = SearchCadenceService(None, settings)  # type: ignore[arg-type]
    now = datetime(2026, 11, 4, 12, 37, tzinfo=UTC)
    row = SearchCadenceSetting(
        family="CATCHALL_SENDERS",
        cadence_seconds=SearchCadence.ONE_HOUR,
        last_slot_start=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
        last_run_at=datetime(2026, 11, 4, 12, 20, tzinfo=UTC),
        last_run_outcome="failed",
        updated_at=now,
    )
    assert service.next_dispatch_at(row, now) == datetime(2026, 11, 4, 13, 2, tzinfo=UTC)


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 11, 4, 19, 2, tzinfo=UTC),
        datetime(2026, 11, 4, 19, 24, tzinfo=UTC),
        datetime(2026, 11, 4, 19, 48, tzinfo=UTC),
        datetime(2026, 11, 4, 20, 4, tzinfo=UTC),
    ],
)
@pytest.mark.parametrize("claimed_age_seconds", [0, 300, 3600, 86_400])
def test_a_five_minute_cadence_never_shows_more_than_one_slot_and_settle_ahead(
    now: datetime, claimed_age_seconds: int
) -> None:
    # The read model advances from max(next slot after the last claimed slot,
    # the current settled slot), so for an enabled five-minute family the cell
    # can never be more than one cadence plus the settle margin in the future,
    # whatever the last run left behind. A 38-minute figure therefore cannot
    # belong to a 5-min row.
    settings = Settings(search_cadence_settle_seconds=120)
    service = SearchCadenceService(None, settings)  # type: ignore[arg-type]
    claimed = slot_start(
        now - timedelta(seconds=claimed_age_seconds),
        int(SearchCadence.FIVE_MINUTES),
        0,
    )
    row = SearchCadenceSetting(
        family="CATCHALL_SENDERS",
        cadence_seconds=SearchCadence.FIVE_MINUTES,
        last_slot_start=claimed,
        updated_at=now,
    )
    assert service.next_dispatch_at(row, now) <= now + timedelta(
        seconds=int(SearchCadence.FIVE_MINUTES) + settings.search_cadence_settle_seconds
    )


def test_next_dispatch_message_is_set_only_while_the_last_run_failed() -> None:
    settings = Settings(search_cadence_settle_seconds=120)
    service = SearchCadenceService(None, settings)  # type: ignore[arg-type]
    now = datetime(2026, 11, 4, 19, 48, tzinfo=UTC)
    row = SearchCadenceSetting(
        family="CATCHALL_SENDERS",
        cadence_seconds=SearchCadence.ONE_HOUR,
        last_slot_start=datetime(2026, 11, 4, 19, 0, tzinfo=UTC),
        last_run_at=datetime(2026, 11, 4, 19, 24, tzinfo=UTC),
        last_run_outcome="failed",
        updated_at=now,
    )
    next_at = service.next_dispatch_at(row, now)
    assert next_at == datetime(2026, 11, 4, 20, 2, tzinfo=UTC)
    message = service.next_dispatch_message(row, next_at)
    assert message is not None
    assert message.code == "search_cadence.next_dispatch.after_failed_run"
    assert message.params == {
        "at": datetime(2026, 11, 4, 19, 24, tzinfo=UTC),
        "cadence": 3600,
        "next": datetime(2026, 11, 4, 20, 2, tzinfo=UTC),
    }
    assert render(message) == (
        "The run at 2026-11-04 19:24:00 UTC failed; the family runs again at its "
        "next 1 h slot, 2026-11-04 20:02:00 UTC, and is not retried before it."
    )

    row.last_run_outcome = "ok"
    assert service.next_dispatch_message(row, next_at) is None
    row.last_run_outcome = None
    assert service.next_dispatch_message(row, next_at) is None


def test_an_off_family_has_no_reason_even_after_a_failed_run() -> None:
    # FIX-48: a family switched off is never dispatched, so the page shows only
    # its dash; an old failed run must not add "Not retried after the failed run".
    settings = Settings(
        search_cadence_settle_seconds=120,
        rule_match_collection_enabled=False,
    )
    service = SearchCadenceService(None, settings)  # type: ignore[arg-type]
    now = datetime(2026, 11, 4, 19, 48, tzinfo=UTC)
    row = SearchCadenceSetting(
        family="RULE_MATCH",
        cadence_seconds=SearchCadence.ONE_HOUR,
        last_slot_start=datetime(2026, 11, 4, 19, 0, tzinfo=UTC),
        last_run_at=datetime(2026, 11, 4, 19, 24, tzinfo=UTC),
        last_run_outcome="failed",
        updated_at=now,
    )
    next_at = service.next_dispatch_at(row, now)
    assert service.next_dispatch_message(row, next_at) is None

    # The same failed run keeps the existing reason once the family is on.
    enabled = settings.model_copy(update={"rule_match_collection_enabled": True})
    message = SearchCadenceService(None, enabled).next_dispatch_message(  # type: ignore[arg-type]
        row, next_at
    )
    assert message is not None
    assert message.code == "search_cadence.next_dispatch.after_failed_run"
    assert message.params["next"] == next_at


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
    # QRADAR_HEALTH stores one window per run (the cadence), not fixed-width buckets.
    assert bucket_seconds(SearchFamily.QRADAR_HEALTH, settings) is None
    for family in SearchFamily:
        if family not in (SearchFamily.ANOMALY_EXPLANATION, SearchFamily.QRADAR_HEALTH):
            assert bucket_seconds(family, settings) is not None


def test_family_enabled_matches_every_collection_switch() -> None:
    from app.models.enums import SearchFamily

    settings = Settings(
        waf_collection_enabled=False,
        explanation_enabled=False,
        rule_match_collection_enabled=False,
        ips_collection_enabled=False,
        pw_collection_enabled=False,
        catchall_collection_enabled=False,
        geo_collection_enabled=False,
        qradar_health_collection_enabled=False,
    )
    assert family_enabled(SearchFamily.PHASE_A_VOLUME, settings) is True
    assert all(
        family_enabled(family, settings) is False
        for family in SearchFamily
        if family is not SearchFamily.PHASE_A_VOLUME
    )

    enabled = settings.model_copy(
        update={
            "waf_collection_enabled": True,
            "explanation_enabled": True,
            "rule_match_collection_enabled": True,
            "ips_collection_enabled": True,
            "pw_collection_enabled": True,
            "catchall_collection_enabled": True,
            "geo_collection_enabled": True,
            "qradar_health_collection_enabled": True,
        }
    )
    assert all(family_enabled(family, enabled) for family in SearchFamily)
