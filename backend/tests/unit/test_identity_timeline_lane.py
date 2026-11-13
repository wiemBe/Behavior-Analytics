"""The identity lane reports what was collected. It never calls a missing name absent."""

from __future__ import annotations

from app.i18n.messages import MessageRef, render
from app.schemas.entity_timeline import EntityTimelineLane
from app.schemas.related_activity import RelatedSectionState
from app.services.entity_timeline import REASON_NOT_OBSERVED, LaneEvidence, decide_lane


def _lane(
    *,
    item_count: int = 0,
    enabled: bool = True,
    collected: bool = True,
    complete: bool = True,
    proves_absence: bool = False,
) -> object:
    return decide_lane(
        LaneEvidence(
            EntityTimelineLane.IDENTITY_TOP,
            item_count,
            enabled=enabled,
            collected=collected,
            complete=complete,
            proves_absence=proves_absence,
            incomplete_reason=REASON_NOT_OBSERVED,
            index_complete=True,
        )
    )


def test_a_missing_name_is_not_observed_and_is_not_an_absent_zero() -> None:
    lane = _lane()
    assert lane.state is RelatedSectionState.PARTIAL
    assert lane.total is None
    assert lane.reason_code == "entity_timeline.reason.not_observed"
    text = render(MessageRef(lane.reason_code))
    assert "not observed in collected data" in text
    assert "absent" not in text.lower()


def test_the_lane_does_not_say_not_observed_when_names_were_not_collected() -> None:
    off = _lane(enabled=False)
    assert off.state is RelatedSectionState.NOT_COLLECTED
    assert off.reason_code == "entity_timeline.reason.family_off"
    assert off.total is None
    uncovered = _lane(collected=False)
    assert uncovered.state is RelatedSectionState.NOT_COLLECTED
    assert uncovered.reason_code == "entity_timeline.reason.no_coverage"
    assert uncovered.total is None


def test_a_truncated_empty_lane_stays_partial() -> None:
    lane = _lane(complete=False)
    assert lane.state is RelatedSectionState.PARTIAL
    assert lane.total is None
    assert lane.reason_code == "entity_timeline.reason.not_observed"


def test_stored_mentions_can_be_ok_without_claiming_a_zero() -> None:
    lane = _lane(item_count=2)
    assert lane.state is RelatedSectionState.OK
    assert lane.total == 2
    assert lane.reason_code is None
