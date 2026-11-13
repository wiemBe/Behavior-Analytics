"""One Ariel search the platform submitted, without the query or its rows.

The row answers "how much load did we put on the SIEM": who asked, how the
search ended, how long the app waited, and the numeric cost fields QRadar's
status payload actually carries. It does not store AQL, result rows, or raw
values. It is not a hypertable; the daily deleter prunes it by ``submitted_at``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin

#: Closed set. A new collector passes one of these; free text is not a caller.
#: The log-source volume family is named for what it collects, not for the
#: project phase that built it (decision 2026-11-02: a reader never sees
#: "Phase A"). The persisted `SearchFamily` cadence value is a different,
#: older label and stays as it is.
CALLERS: tuple[str, ...] = (
    "LOG_SOURCE_VOLUME",
    "ANOMALY_EXPLANATION",
    "RULE_MATCH",
    "WAF_COLLECTION",
    "IPS_COLLECTION",
    "PROPERTY_WATCH",
    "CATCHALL_SENDERS",
    "GEO_COLLECTION",
    "QRADAR_HEALTH",
    "HISTORY",
    "PREVIEW",
    "SCHEDULED_SEARCH",
    "MANUAL_RUN",
    "DOCTOR_AQL",
    "ACCESS_CHECK",
    "IDENTITY_COLLECTION",
)

#: IN_FLIGHT is the row written at submit time and closed by the terminal
#: write. REFUSED is the permit refusal (the spec's "REFUSED-by-permit"):
#: nothing was sent, so it never has an in-flight row. ABANDONED is the
#: reaper's close of a row whose worker died before the terminal write: the
#: search did load QRadar, and a later terminal write overwrites it.
OUTCOMES: tuple[str, ...] = (
    "IN_FLIGHT",
    "COMPLETED",
    "ERROR",
    "CANCELED",
    "TIMEOUT",
    "REFUSED",
    "PROVIDER_UNAVAILABLE",
    "ABANDONED",
)

#: The outcomes that close a search. Everything else is still running.
TERMINAL_OUTCOMES: tuple[str, ...] = (
    "COMPLETED",
    "ERROR",
    "CANCELED",
    "TIMEOUT",
    "REFUSED",
    "PROVIDER_UNAVAILABLE",
    "ABANDONED",
)


def _sql_in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{item}'" for item in values)
    return f"{column} IN ({quoted})"


class ArielSearchRun(UUIDPrimaryKeyMixin, Base):
    """Load record for one submitted Ariel search.

    ``instance_id`` and ``scheduled_search_id`` are plain UUIDs, not foreign
    keys. The insert commits on its own connection so a later rollback of the
    collection transaction cannot drop it, and that connection cannot see a
    row the caller has not committed yet. A foreign key would make the insert
    fail, the failure is swallowed, and the load row would be the thing lost.
    """

    __tablename__ = "ariel_search_run"
    __table_args__ = (
        CheckConstraint("wall_ms >= 0", name="wall_ms_non_negative"),
        CheckConstraint("polls >= 0", name="polls_non_negative"),
        CheckConstraint(_sql_in("caller", CALLERS), name="caller_known"),
        CheckConstraint(_sql_in("outcome", OUTCOMES), name="outcome_known"),
        # The admin read is per instance when one is named.
        Index("ix_ariel_search_run_instance_id_submitted_at", "instance_id", "submitted_at"),
        # The summary window is global: it does not filter on instance_id, so
        # the composite index's left prefix cannot serve it.
        Index("ix_ariel_search_run_submitted_at", "submitted_at"),
    )

    instance_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    caller: Mapped[str] = mapped_column(String(64), nullable=False)
    scheduled_search_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    #: Integer query version only. Rule-match versions are strings and stay null.
    query_version: Mapped[int | None] = mapped_column(Integer)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Null while the search is ``IN_FLIGHT``; the terminal write sets it.
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Null while the search is ``IN_FLIGHT``. A measured zero is a zero; an
    #: unfinished search is null, never zero.
    wall_ms: Mapped[int | None] = mapped_column(Integer)
    #: QRadar ``query_execution_time``, milliseconds. Null when the status
    #: payload did not carry it. A present 0 is a measurement.
    qradar_execution_ms: Mapped[int | None] = mapped_column(BigInteger)
    qradar_record_count: Mapped[int | None] = mapped_column(BigInteger)
    processed_record_count: Mapped[int | None] = mapped_column(BigInteger)
    data_file_count: Mapped[int | None] = mapped_column(BigInteger)
    data_total_size: Mapped[int | None] = mapped_column(BigInteger)
    compressed_data_file_count: Mapped[int | None] = mapped_column(BigInteger)
    compressed_data_total_size: Mapped[int | None] = mapped_column(BigInteger)
    index_file_count: Mapped[int | None] = mapped_column(BigInteger)
    index_total_size: Mapped[int | None] = mapped_column(BigInteger)
    #: Rows the app returned from the results read. Null when results were not
    #: fetched. Distinct from ``qradar_record_count`` (the status field).
    record_count: Mapped[int | None] = mapped_column(BigInteger)
    range_seconds: Mapped[int | None] = mapped_column(Integer)
    polls: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
