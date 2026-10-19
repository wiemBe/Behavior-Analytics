"""History backfill: the operator's answer and the per-scope reading state.

HISTORY-BACKFILL §B.7.1. `HistoryBackfillDecision` is one row per console, written once by
the installer or an administrator. `HistoryBackfillScope` is one row per (console, family,
scope key), created when a scope is requested. The family and state vocabularies are the
shared core's (`app.history.state`); the CHECKs are built from them so a new member cannot
be forgotten here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.history.origin import HistoryOrigin
from app.history.state import SCOPE_TABLE_FAMILIES, HistoryFamily, HistoryState
from app.models.base import Base
from app.models.enums import HistoryDecisionAnswer, HistoryDecisionSource

#: The shortest window a decision may record: one visit per hour of the week (§B.6.5).
MIN_DECISION_DAYS = 7

#: Families whose scope key is a QRadar log source (non-empty); the others use ''.
PER_SOURCE_FAMILIES: tuple[HistoryFamily, ...] = (HistoryFamily.WAF, HistoryFamily.IPS)

#: A scope row exists only once a scope is requested, so `NOT_REQUESTED` is not stored.
SCOPE_STORED_STATES: tuple[HistoryState, ...] = tuple(
    state for state in HistoryState if state is not HistoryState.NOT_REQUESTED
)
SCOPE_STORED_FAMILIES: tuple[HistoryFamily, ...] = tuple(
    family for family in HistoryFamily if family in SCOPE_TABLE_FAMILIES
)


def _in_list(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


#: The CHECK of the `origin` column on the four tables a family may change (§B.7.1).
ORIGIN_CHECK = f"origin IN {_in_list(tuple(o.value for o in HistoryOrigin))}"
ANSWER_CHECK = f"answer IN {_in_list(tuple(m.value for m in HistoryDecisionAnswer))}"
SOURCE_CHECK = f"source IN {_in_list(tuple(m.value for m in HistoryDecisionSource))}"
DAYS_IFF_YES_CHECK = f"(answer = '{HistoryDecisionAnswer.YES.value}') = (days IS NOT NULL)"
DAYS_MINIMUM_CHECK = f"days IS NULL OR days >= {MIN_DECISION_DAYS}"
FAMILY_CHECK = f"family IN {_in_list(tuple(f.value for f in SCOPE_STORED_FAMILIES))}"
STATE_CHECK = f"state IN {_in_list(tuple(s.value for s in SCOPE_STORED_STATES))}"
SCOPE_KEY_CHECK = (
    f"(family IN {_in_list(tuple(f.value for f in PER_SOURCE_FAMILIES))}) = (scope_key <> '')"
)


class HistoryBackfillDecision(Base):
    """The answer for one console. The installer writes it once and never changes it."""

    __tablename__ = "history_backfill_decision"
    __table_args__ = (
        CheckConstraint(ANSWER_CHECK, name="answer_known"),
        CheckConstraint(SOURCE_CHECK, name="source_known"),
        CheckConstraint(DAYS_IFF_YES_CHECK, name="days_iff_yes"),
        CheckConstraint(DAYS_MINIMUM_CHECK, name="days_minimum"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    answer: Mapped[str] = mapped_column(String(3), nullable=False)
    #: The window a YES reads (28 by default); NULL for a NO.
    days: Mapped[int | None] = mapped_column(Integer)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The principal subject, or `installer`.
    decided_by: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    #: A later NO-to-YES change (HB-8).
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(255))


class HistoryBackfillScope(Base):
    """One scope's reading state: a console and family, or one source of WAF or IPS."""

    __tablename__ = "history_backfill_scope"
    __table_args__ = (
        CheckConstraint(FAMILY_CHECK, name="family_known"),
        CheckConstraint(STATE_CHECK, name="state_known"),
        CheckConstraint(SCOPE_KEY_CHECK, name="scope_key_shape"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    family: Mapped[str] = mapped_column(String(16), primary_key=True)
    #: '' for a console-wide family, the QRadar log-source id for WAF and IPS.
    scope_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    days_requested: Mapped[int | None] = mapped_column(Integer)
    #: The fitted window (§B.6.5).
    days_target: Mapped[int | None] = mapped_column(Integer)
    target_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    floor: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anchor: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Words, never a value text.
    reason: Mapped[str | None] = mapped_column(String(255))
    #: The planner's carried progress (`app.history.progress_codec`).
    progress: Mapped[dict] = mapped_column(JSONB, nullable=False)
