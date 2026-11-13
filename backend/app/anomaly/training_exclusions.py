"""Analyst baseline training exclusions: interval guards, storage helpers, bulk load (SOC-13a).

An exclusion is a half-open UTC interval `[starts_at, ends_at)` for one training subject
and one baseline scope. The guards here are the design's section 2; the routes (SOC-13b)
and the training integration (SOC-13c) build on them. Nothing in this module calls
QRadar, and nothing reads or rewrites an aggregate.

`load_active` is the one read a training run makes: a single SELECT for every due
subject, coalesced in memory, so the SQL cost stays constant (AGENTS 2.9).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.training_exclusion import MAX_INTERVAL_DAYS, BaselineTrainingExclusion

CODE_PREFIX = "baseline_exclusion."
ANOMALY_ACTIVE = CODE_PREFIX + "anomaly_active"
FAMILY_UNSUPPORTED = CODE_PREFIX + "family_unsupported"
INTERVAL_INVALID = CODE_PREFIX + "interval_invalid"
INTERVAL_NOT_WIDENED = CODE_PREFIX + "interval_not_widened"
INTERVAL_TOO_LONG = CODE_PREFIX + "interval_too_long"
OVERLAP = CODE_PREFIX + "overlap"
LIMIT_REACHED = CODE_PREFIX + "limit_reached"
NOT_FOUND = CODE_PREFIX + "not_found"
ALREADY_PROTECTED = CODE_PREFIX + "already_protected"

#: Relevant, unrevoked rows allowed per subject and scope.
MAX_RELEVANT_PER_SCOPE = 50
MAX_INTERVAL = timedelta(days=MAX_INTERVAL_DAYS)

#: The scope Phase A source-volume baselines train on (design section 1).
LOG_SOURCE_VOLUME_SCOPE = "log_source:volume"
#: One scope for every metric of a WAF endpoint baseline (SOC-13e).
#: Design section 1 allows separate request and ratio scopes. The server derives
#: the scope, and the client cannot pick a metric, so one incident interval is
#: kept out of the request-count cell and every ratio cell together.
WAF_ENDPOINT_SCOPE = "waf:endpoint"

Span = tuple[datetime, datetime]
SpanKey = tuple[str, uuid.UUID, str]


class ExclusionError(Exception):
    """A guard refused the request; `code` is one of the constants above."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CreateResult:
    row: BaselineTrainingExclusion
    #: False when an exact active duplicate already existed (the route answers 200).
    created: bool


@dataclass(frozen=True)
class RevokeResult:
    row: BaselineTrainingExclusion
    #: False when the row was already revoked; nothing was changed.
    changed: bool


def validate_interval(
    start: datetime,
    end: datetime,
    *,
    bucket_seconds: int,
    incident_start: datetime,
    incident_end: datetime,
) -> str | None:
    """Return the coded error for a requested interval, or None when it is acceptable.

    The client may only widen the incident span: the interval must cover it, be aligned
    to the family's bucket width, ordered, and at most seven days long.
    """
    if bucket_seconds <= 0:
        return INTERVAL_INVALID
    if start.tzinfo is None or end.tzinfo is None:
        return INTERVAL_INVALID
    if start >= end:
        return INTERVAL_INVALID
    if start.timestamp() % bucket_seconds != 0 or end.timestamp() % bucket_seconds != 0:
        return INTERVAL_INVALID
    if end - start > MAX_INTERVAL:
        return INTERVAL_TOO_LONG
    if start > incident_start or end < incident_end:
        return INTERVAL_NOT_WIDENED
    return None


async def create(
    session: AsyncSession,
    *,
    origin_family: str,
    origin_anomaly_id: uuid.UUID,
    subject_kind: str,
    subject_id: uuid.UUID,
    scope_key: str,
    starts_at: datetime,
    ends_at: datetime,
    reason: str,
    created_by: str,
    lookback_start: datetime,
) -> CreateResult:
    """Insert a row, or return the exact active duplicate.

    `lookback_start` is the family's current maximum lookback start: a row is relevant to
    the 50-row cap while its end lies after it. Raises `ExclusionError` for a partial
    overlap with an active row (adjacent intervals are fine) or a full scope. Flushes; the
    caller owns the transaction.
    """
    same_scope = (
        BaselineTrainingExclusion.subject_kind == subject_kind,
        BaselineTrainingExclusion.subject_id == subject_id,
        BaselineTrainingExclusion.scope_key == scope_key,
        BaselineTrainingExclusion.revoked_at.is_(None),
    )
    overlapping = (
        await session.scalars(
            select(BaselineTrainingExclusion).where(
                *same_scope,
                BaselineTrainingExclusion.starts_at < ends_at,
                BaselineTrainingExclusion.ends_at > starts_at,
            )
        )
    ).all()
    for row in overlapping:
        if row.starts_at == starts_at and row.ends_at == ends_at:
            return CreateResult(row=row, created=False)
    if overlapping:
        raise ExclusionError(OVERLAP)
    relevant = await session.scalar(
        select(func.count())
        .select_from(BaselineTrainingExclusion)
        .where(*same_scope, BaselineTrainingExclusion.ends_at > lookback_start)
    )
    if (relevant or 0) >= MAX_RELEVANT_PER_SCOPE:
        raise ExclusionError(LIMIT_REACHED)
    row = BaselineTrainingExclusion(
        origin_family=origin_family,
        origin_anomaly_id=origin_anomaly_id,
        subject_kind=subject_kind,
        subject_id=subject_id,
        scope_key=scope_key,
        starts_at=starts_at,
        ends_at=ends_at,
        reason=reason,
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    return CreateResult(row=row, created=True)


async def revoke(
    session: AsyncSession,
    exclusion_id: uuid.UUID,
    *,
    revoked_by: str,
    reason: str,
    now: datetime | None = None,
) -> RevokeResult:
    """Revoke a row. Idempotent: an already revoked row is returned unchanged.

    The three revocation fields are set together, as the database check requires.
    """
    row = await session.get(BaselineTrainingExclusion, exclusion_id)
    if row is None:
        raise ExclusionError(NOT_FOUND)
    if row.revoked_at is not None:
        return RevokeResult(row=row, changed=False)
    row.revoked_by = revoked_by
    row.revoked_at = now or datetime.now(UTC)
    row.revocation_reason = reason
    await session.flush()
    return RevokeResult(row=row, changed=True)


async def find_active_exact(
    session: AsyncSession,
    *,
    subject_kind: str,
    subject_id: uuid.UUID,
    scope_key: str,
    starts_at: datetime,
    ends_at: datetime,
) -> BaselineTrainingExclusion | None:
    """The active row with exactly these bounds, for the re-read after a lost race."""
    return await session.scalar(
        select(BaselineTrainingExclusion).where(
            BaselineTrainingExclusion.subject_kind == subject_kind,
            BaselineTrainingExclusion.subject_id == subject_id,
            BaselineTrainingExclusion.scope_key == scope_key,
            BaselineTrainingExclusion.starts_at == starts_at,
            BaselineTrainingExclusion.ends_at == ends_at,
            BaselineTrainingExclusion.revoked_at.is_(None),
        )
    )


def coalesce(spans: Sequence[Span]) -> list[Span]:
    """Merge overlapping or touching half-open spans, sorted by start."""
    merged: list[Span] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


async def load_active(
    session: AsyncSession,
    subject_kind: str,
    subject_ids: Sequence[uuid.UUID],
    scope_key: str,
    lookback_start: datetime,
    lookback_end: datetime,
) -> dict[SpanKey, list[Span]]:
    """One SELECT for many subjects: unrevoked spans meeting the lookback, coalesced.

    A subject with no exclusion has no key. The empty id list issues no statement.
    """
    if not subject_ids:
        return {}
    rows = await session.execute(
        select(
            BaselineTrainingExclusion.subject_id,
            BaselineTrainingExclusion.starts_at,
            BaselineTrainingExclusion.ends_at,
        ).where(
            BaselineTrainingExclusion.subject_kind == subject_kind,
            BaselineTrainingExclusion.scope_key == scope_key,
            BaselineTrainingExclusion.subject_id.in_(list(subject_ids)),
            BaselineTrainingExclusion.revoked_at.is_(None),
            BaselineTrainingExclusion.starts_at < lookback_end,
            BaselineTrainingExclusion.ends_at > lookback_start,
        )
    )
    grouped: dict[uuid.UUID, list[Span]] = defaultdict(list)
    for subject_id, starts_at, ends_at in rows:
        grouped[subject_id].append((starts_at, ends_at))
    return {
        (subject_kind, subject_id, scope_key): coalesce(spans)
        for subject_id, spans in grouped.items()
    }
