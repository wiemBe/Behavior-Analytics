"""SOC-1c (D-55): the stale triage rule, its note, its setting, its beat entry."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.enums import TriageDisposition, TriageStatus
from app.schemas.triage import TriagePatchIn
from app.services.stale_triage import stale_close_note, stale_eligible
from app.workers import tasks
from app.workers.celery_app import celery_app

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=4)
YESTERDAY = NOW - timedelta(days=1)


def eligible(**kw):  # type: ignore[no-untyped-def]
    values = dict(
        detected_at=OLD,
        triage_status=None,
        triage_updated_at=None,
        now=NOW,
        days=3,
    )
    values.update(kw)
    return stale_eligible(**values)


class TestEligibility:
    def test_an_old_anomaly_without_a_row_is_eligible(self) -> None:
        assert eligible() is True

    def test_an_old_new_row_untouched_for_the_window_is_eligible(self) -> None:
        assert eligible(triage_status=TriageStatus.NEW, triage_updated_at=OLD) is True

    def test_exactly_the_window_old_is_old_enough(self) -> None:
        edge = NOW - timedelta(days=3)
        assert eligible(detected_at=edge) is True
        assert eligible(detected_at=edge + timedelta(seconds=1)) is False

    def test_a_young_anomaly_is_not_eligible(self) -> None:
        assert eligible(detected_at=YESTERDAY) is False

    def test_a_new_row_reopened_recently_is_not_eligible(self) -> None:
        assert (
            eligible(triage_status=TriageStatus.NEW, triage_updated_at=YESTERDAY) is False
        )

    def test_a_new_row_with_an_unknown_clock_is_not_eligible(self) -> None:
        assert eligible(triage_status=TriageStatus.NEW, triage_updated_at=None) is False

    @pytest.mark.parametrize("status", [TriageStatus.IN_PROGRESS, TriageStatus.CLOSED])
    def test_a_taken_or_closed_item_is_never_eligible(self, status: TriageStatus) -> None:
        assert eligible(triage_status=status, triage_updated_at=OLD) is False

    def test_zero_days_disables_it(self) -> None:
        assert eligible(days=0, detected_at=NOW - timedelta(days=900)) is False

    def test_a_negative_window_is_refused(self) -> None:
        with pytest.raises(ValueError):
            eligible(days=-1)


class TestNote:
    def test_the_note_carries_the_day_count(self) -> None:
        assert stale_close_note(3) == "No analyst took this anomaly within 3 days."
        assert stale_close_note(14) == "No analyst took this anomaly within 14 days."

    def test_one_day_is_singular(self) -> None:
        assert stale_close_note(1) == "No analyst took this anomaly within 1 day."

    def test_a_negative_count_is_refused(self) -> None:
        with pytest.raises(ValueError):
            stale_close_note(-1)


class TestSetting:
    def test_the_default_is_three_days(self) -> None:
        assert Settings(_env_file=None).triage_stale_close_days == 3

    def test_zero_is_allowed_and_negative_is_not(self) -> None:
        assert Settings(_env_file=None, triage_stale_close_days=0).triage_stale_close_days == 0
        with pytest.raises(ValidationError):
            Settings(_env_file=None, triage_stale_close_days=-1)


class TestPatchSchema:
    def test_an_analyst_cannot_choose_stale(self) -> None:
        with pytest.raises(ValidationError, match="STALE"):
            TriagePatchIn(
                status=TriageStatus.CLOSED,
                disposition=TriageDisposition.STALE,
                expected_version=0,
            )

    def test_the_other_dispositions_still_validate(self) -> None:
        body = TriagePatchIn(
            status=TriageStatus.CLOSED,
            disposition=TriageDisposition.NOISE,
            expected_version=0,
        )
        assert body.disposition is TriageDisposition.NOISE


class TestBeat:
    def test_the_task_is_registered_and_runs_hourly(self) -> None:
        entry = celery_app.conf.beat_schedule["close-stale-triage"]
        assert entry["task"] == "close_stale_triage"
        assert entry["schedule"] == 3600.0
        assert tasks.close_stale_triage_task.name == "close_stale_triage"
        assert "close_stale_triage" in celery_app.tasks

    def test_zero_days_returns_disabled_without_opening_a_session(self, monkeypatch) -> None:
        settings = Settings(_env_file=None, triage_stale_close_days=0)
        monkeypatch.setattr(tasks, "get_settings", lambda: settings)

        def boom():  # type: ignore[no-untyped-def]
            raise AssertionError("a disabled job must not open a session")

        monkeypatch.setattr(tasks, "get_sessionmaker", boom)
        result = asyncio.run(tasks.close_stale_triage())
        assert result["status"] == "disabled"
        assert result["closed"] == 0
