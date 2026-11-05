"""SOC-1a triage transition rules, pinned without a database.

The rules are a pure function over the current row and the PATCH body, so every
allowed and refused transition is tested here. The API tests then prove the route
maps each exception to the right status code; they do not re-derive the rules.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.models.enums import (
    AnomalyFamily,
    TriageDisposition,
    TriageEventKind,
    TriageStatus,
)
from app.models.triage import AnomalyTriage
from app.schemas.triage import TriageCommentIn, TriagePatchIn
from app.services.anomaly_triage import TriageInvalid, plan_patch

ANOMALY_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def patch(**kwargs: object) -> TriagePatchIn:
    return TriagePatchIn(expected_version=kwargs.pop("expected_version", 0), **kwargs)  # type: ignore[arg-type]


def row(
    *,
    status: TriageStatus = TriageStatus.IN_PROGRESS,
    owner: str | None = None,
    disposition: TriageDisposition | None = None,
    note: str | None = None,
    version: int = 3,
) -> AnomalyTriage:
    return AnomalyTriage(
        family=AnomalyFamily.LOG_SOURCE,
        anomaly_id=ANOMALY_ID,
        owner=owner,
        status=status,
        disposition=disposition,
        disposition_note=note,
        updated_by="previous",
        version=version,
    )


# ------------------------------------------------------------------ happy paths
def test_assigning_an_owner_to_an_untriaged_anomaly() -> None:
    plan = plan_patch(None, patch(owner="analyst"))
    assert plan.owner == "analyst"
    assert plan.status is TriageStatus.NEW
    assert [(e.kind, e.from_value, e.to_value) for e in plan.events] == [
        (TriageEventKind.OWNER, None, "analyst")
    ]


def test_starting_work_writes_one_status_event_from_new() -> None:
    plan = plan_patch(None, patch(status=TriageStatus.IN_PROGRESS))
    assert plan.status is TriageStatus.IN_PROGRESS
    assert [(e.kind, e.from_value, e.to_value) for e in plan.events] == [
        (TriageEventKind.STATUS, "NEW", "IN_PROGRESS")
    ]


def test_closing_directly_from_new_with_a_disposition() -> None:
    plan = plan_patch(
        None,
        patch(status=TriageStatus.CLOSED, disposition=TriageDisposition.NOISE),
    )
    assert plan.status is TriageStatus.CLOSED
    assert plan.disposition is TriageDisposition.NOISE
    assert [e.kind for e in plan.events] == [
        TriageEventKind.STATUS,
        TriageEventKind.DISPOSITION,
    ]


def test_closing_an_in_progress_item_with_an_existing_disposition() -> None:
    """The disposition may already be recorded; closing then writes only STATUS."""
    plan = plan_patch(
        row(disposition=TriageDisposition.NOISE),
        patch(status=TriageStatus.CLOSED),
    )
    assert plan.status is TriageStatus.CLOSED
    assert plan.disposition is TriageDisposition.NOISE
    assert [e.kind for e in plan.events] == [TriageEventKind.STATUS]


def test_a_disposition_while_not_closing_is_allowed() -> None:
    """Decision: an analyst may record the disposition before closing the item."""
    plan = plan_patch(
        row(status=TriageStatus.NEW),
        patch(disposition=TriageDisposition.KNOWN_CHANGE),
    )
    assert plan.status is TriageStatus.NEW
    assert plan.disposition is TriageDisposition.KNOWN_CHANGE
    assert [(e.kind, e.to_value) for e in plan.events] == [
        (TriageEventKind.DISPOSITION, "KNOWN_CHANGE")
    ]


def test_reopening_a_closed_item_requires_an_explicit_status_and_keeps_it() -> None:
    plan = plan_patch(
        row(
            status=TriageStatus.CLOSED,
            disposition=TriageDisposition.TRUE_POSITIVE,
            note="escalated",
        ),
        patch(status=TriageStatus.IN_PROGRESS),
    )
    assert plan.status is TriageStatus.IN_PROGRESS
    # The disposition and its note are history, not something reopening erases.
    assert plan.disposition is TriageDisposition.TRUE_POSITIVE
    assert plan.disposition_note == "escalated"
    assert [(e.kind, e.from_value, e.to_value) for e in plan.events] == [
        (TriageEventKind.STATUS, "CLOSED", "IN_PROGRESS")
    ]


def test_a_closed_item_may_change_its_disposition_without_reopening() -> None:
    plan = plan_patch(
        row(status=TriageStatus.CLOSED, disposition=TriageDisposition.NOISE),
        patch(status=TriageStatus.CLOSED, disposition=TriageDisposition.KNOWN_CHANGE),
    )
    assert plan.status is TriageStatus.CLOSED
    assert plan.disposition is TriageDisposition.KNOWN_CHANGE
    assert [e.kind for e in plan.events] == [TriageEventKind.DISPOSITION]


def test_a_new_disposition_without_a_note_clears_the_old_note() -> None:
    """The note explained the old disposition; carrying it over would misattribute it."""
    plan = plan_patch(
        row(disposition=TriageDisposition.NOISE, note="scanner"),
        patch(
            status=TriageStatus.CLOSED,
            disposition=TriageDisposition.TRUE_POSITIVE,
        ),
    )
    assert plan.disposition_note is None
    event = next(e for e in plan.events if e.kind is TriageEventKind.DISPOSITION)
    assert event.comment is None


def test_a_note_change_without_a_disposition_change_writes_a_note_event() -> None:
    plan = plan_patch(
        row(disposition=TriageDisposition.NOISE, note="old"),
        patch(disposition_note="new"),
    )
    assert plan.disposition_note == "new"
    assert [(e.kind, e.comment) for e in plan.events] == [(TriageEventKind.NOTE, "new")]


def test_clearing_a_note_writes_a_note_event_with_no_text() -> None:
    plan = plan_patch(
        row(disposition=TriageDisposition.NOISE, note="old"),
        patch(disposition_note=None),
    )
    assert plan.disposition_note is None
    assert [(e.kind, e.comment) for e in plan.events] == [(TriageEventKind.NOTE, None)]


def test_a_no_op_patch_writes_no_event() -> None:
    plan = plan_patch(row(owner="analyst"), patch(owner="analyst"))
    assert plan.events == []


# --------------------------------------------------------------- refused paths
def test_closing_without_a_disposition_is_refused() -> None:
    with pytest.raises(TriageInvalid, match="requires a disposition"):
        plan_patch(None, patch(status=TriageStatus.CLOSED))


def test_closing_while_clearing_the_disposition_is_refused() -> None:
    with pytest.raises(TriageInvalid, match="requires a disposition"):
        plan_patch(
            row(status=TriageStatus.IN_PROGRESS, disposition=TriageDisposition.NOISE),
            patch(status=TriageStatus.CLOSED, disposition=None),
        )


def test_an_explicit_new_on_a_started_item_is_refused() -> None:
    with pytest.raises(TriageInvalid, match="status NEW"):
        plan_patch(row(), patch(status=TriageStatus.NEW))


def test_a_null_status_is_refused() -> None:
    with pytest.raises(TriageInvalid, match="status cannot be cleared"):
        plan_patch(row(), patch(status=None))


def test_a_closed_item_cannot_be_changed_without_reopening_it() -> None:
    with pytest.raises(TriageInvalid, match="reopening"):
        plan_patch(
            row(status=TriageStatus.CLOSED, disposition=TriageDisposition.NOISE),
            patch(owner="someone-else"),
        )


def test_an_empty_patch_is_refused() -> None:
    with pytest.raises(TriageInvalid, match="no triage field"):
        plan_patch(row(), patch())


# ------------------------------------------------------------- input validation
def test_a_comment_at_the_bound_is_accepted_and_one_past_is_refused() -> None:
    assert TriageCommentIn(text="x" * 2000).text == "x" * 2000
    with pytest.raises(ValidationError):
        TriageCommentIn(text="x" * 2001)
    with pytest.raises(ValidationError):
        TriageCommentIn(text="   ")


def test_owner_and_note_bounds() -> None:
    assert TriagePatchIn(expected_version=0, owner="o" * 255).owner == "o" * 255
    with pytest.raises(ValidationError):
        TriagePatchIn(expected_version=0, owner="o" * 256)
    assert (
        TriagePatchIn(expected_version=0, disposition_note="n" * 1000).disposition_note
        == "n" * 1000
    )
    with pytest.raises(ValidationError):
        TriagePatchIn(expected_version=0, disposition_note="n" * 1001)


def test_an_empty_owner_clears_it_instead_of_storing_a_blank_subject() -> None:
    assert TriagePatchIn(expected_version=0, owner="  ").owner is None


def test_expected_version_is_required_and_non_negative() -> None:
    with pytest.raises(ValidationError):
        TriagePatchIn(owner="analyst")
    with pytest.raises(ValidationError):
        TriagePatchIn(expected_version=-1, owner="analyst")
