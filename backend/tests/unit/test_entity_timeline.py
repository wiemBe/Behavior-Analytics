"""Pure bounds, cursor and honesty rules for the stored entity timeline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.schemas.entity_timeline import EntityTimelineKind, EntityTimelineLane
from app.schemas.related_activity import RelatedSectionState
from app.services.entity_timeline import (
    DEFAULT_WINDOW,
    MAX_WINDOW,
    REASON_BOUNDED,
    REASON_FAMILY_OFF,
    REASON_INDEX_INCOMPLETE,
    REASON_NO_COVERAGE,
    REASON_OFFENSE_STALE,
    REASON_PAGE_NOT_COLLECTED,
    REASON_PAGE_PARTIAL,
    REASON_PAGE_TRUNCATED,
    REASON_PERMISSION,
    CursorCodec,
    CursorPosition,
    EntityTimelineInputError,
    LaneEvidence,
    decide_lane,
    page_state,
    resolve_entity,
    validate_limit,
    window_bounds,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _error_code(call) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(EntityTimelineInputError) as raised:
        call()
    return raised.value.ref.code


def test_window_defaults_to_24_hours_and_accepts_the_exact_maximum() -> None:
    start, end = window_bounds(None, None, now=NOW)
    assert (start, end) == (NOW - DEFAULT_WINDOW, NOW)
    assert window_bounds(NOW - MAX_WINDOW, NOW) == (NOW - MAX_WINDOW, NOW)


@pytest.mark.parametrize(
    ("start", "end", "code"),
    [
        (NOW, NOW, "entity_timeline.input.window_order"),
        (NOW + timedelta(seconds=1), NOW, "entity_timeline.input.window_order"),
        (
            NOW - MAX_WINDOW - timedelta(microseconds=1),
            NOW,
            "entity_timeline.input.window_max",
        ),
    ],
)
def test_window_refuses_empty_reversed_and_overlong_ranges(
    start: datetime, end: datetime, code: str
) -> None:
    assert _error_code(lambda: window_bounds(start, end)) == code


@pytest.mark.parametrize("limit", [1, 50, 200])
def test_limit_accepts_the_documented_bounds(limit: int) -> None:
    assert validate_limit(limit) == limit


@pytest.mark.parametrize("limit", [0, -1, 201])
def test_limit_refuses_values_outside_the_documented_bounds(limit: int) -> None:
    assert _error_code(lambda: validate_limit(limit)) == "entity_timeline.input.limit"


def test_kind_is_inferred_only_for_an_ip_and_user_text_needs_a_kind() -> None:
    assert resolve_entity(None, " 2001:0db8::1 ") == (
        EntityTimelineKind.IP,
        "2001:0db8::1",
        "2001:db8::1",
    )
    assert resolve_entity("user", " DOMAIN\\Alice ") == (
        EntityTimelineKind.USER,
        "DOMAIN\\Alice",
        "domain\\alice",
    )
    assert _error_code(lambda: resolve_entity(None, "server-1")) == (
        "entity_timeline.input.kind_required"
    )
    assert _error_code(lambda: resolve_entity("device", "server-1")) == (
        "entity_timeline.input.kind_invalid"
    )


def test_cursor_round_trips_and_tampering_is_refused() -> None:
    codec = CursorCodec("test-secret")
    position = CursorPosition(NOW, EntityTimelineLane.WAF_TOP.value, "parent-7")
    token = codec.encode(position)
    assert codec.decode(token) == position

    replacement = "A" if token[-1] != "A" else "B"
    assert _error_code(lambda: codec.decode(token[:-1] + replacement)) == (
        "entity_timeline.input.cursor_invalid"
    )
    assert _error_code(lambda: codec.decode("not-a-cursor")) == (
        "entity_timeline.input.cursor_invalid"
    )


def test_lane_permission_and_missing_collection_never_invent_zero() -> None:
    forbidden = decide_lane(
        LaneEvidence(
            EntityTimelineLane.OFFENSE,
            0,
            permitted=False,
            permission="read:offenses",
        )
    )
    assert forbidden.model_dump() == {
        "source": EntityTimelineLane.OFFENSE,
        "state": RelatedSectionState.NOT_COLLECTED,
        "reason_code": REASON_PERMISSION,
        "total": None,
        "permission": "read:offenses",
    }

    switched_off = decide_lane(
        LaneEvidence(EntityTimelineLane.WAF_TOP, 0, enabled=False)
    )
    assert switched_off.state is RelatedSectionState.NOT_COLLECTED
    assert switched_off.reason_code == REASON_FAMILY_OFF
    assert switched_off.total is None

    missing = decide_lane(
        LaneEvidence(EntityTimelineLane.IPS_TOP, 0, collected=False)
    )
    assert missing.state is RelatedSectionState.NOT_COLLECTED
    assert missing.reason_code == REASON_NO_COVERAGE
    assert missing.total is None


def test_lane_distinguishes_proven_absence_from_a_bounded_non_match() -> None:
    exact = decide_lane(
        LaneEvidence(EntityTimelineLane.EXPLANATION, 0, proves_absence=True)
    )
    assert exact.state is RelatedSectionState.OK
    assert exact.reason_code is None
    assert exact.total == 0

    bounded = decide_lane(LaneEvidence(EntityTimelineLane.IPS_ENTITY, 0))
    assert bounded.state is RelatedSectionState.PARTIAL
    assert bounded.reason_code == REASON_BOUNDED
    assert bounded.total is None


def test_lane_reports_items_but_omits_a_total_when_the_page_is_truncated() -> None:
    complete = decide_lane(LaneEvidence(EntityTimelineLane.IPS_TOP, 4))
    assert complete.state is RelatedSectionState.OK
    assert complete.total == 4

    page_prefix = decide_lane(
        LaneEvidence(EntityTimelineLane.IPS_TOP, 4, count_is_total=False)
    )
    assert page_prefix.state is RelatedSectionState.OK
    assert page_prefix.total is None

    stale = decide_lane(
        LaneEvidence(
            EntityTimelineLane.OFFENSE,
            0,
            freshness=RelatedSectionState.STALE,
        )
    )
    assert stale.state is RelatedSectionState.STALE
    assert stale.reason_code == REASON_OFFENSE_STALE
    assert stale.total is None

    incomplete = decide_lane(
        LaneEvidence(EntityTimelineLane.EXPLANATION, 1, complete=False)
    )
    assert incomplete.state is RelatedSectionState.PARTIAL
    assert incomplete.reason_code == REASON_NO_COVERAGE
    assert incomplete.total == 1

    dangling = decide_lane(
        LaneEvidence(EntityTimelineLane.OFFENSE, 0, dangling_count=1)
    )
    assert dangling.state is RelatedSectionState.PARTIAL
    assert dangling.reason_code == REASON_INDEX_INCOMPLETE
    assert dangling.total is None


def test_page_state_preserves_truncation_then_collection_honesty() -> None:
    ok = decide_lane(LaneEvidence(EntityTimelineLane.EXPLANATION, 0, proves_absence=True))
    missing = decide_lane(LaneEvidence(EntityTimelineLane.WAF_TOP, 0, collected=False))

    assert page_state([ok], truncated=False) == (RelatedSectionState.OK, None)
    assert page_state([missing], truncated=False) == (
        RelatedSectionState.NOT_COLLECTED,
        REASON_PAGE_NOT_COLLECTED,
    )
    assert page_state([ok, missing], truncated=False) == (
        RelatedSectionState.PARTIAL,
        REASON_PAGE_PARTIAL,
    )
    assert page_state([ok], truncated=True) == (
        RelatedSectionState.TRUNCATED,
        REASON_PAGE_TRUNCATED,
    )
