"""The triage queue read model (SOC-4b, design `SOC-4.design.md` §2.4 and §3).

One list of unfinished work from five sources: anomalies (four families), alerts that are not
an anomaly's alert, open offenses, sources awaiting classification review, and catch-all
senders awaiting review. Membership is state-based; "new since" is a flag laid over the rows
and never adds or removes one.

Each source is read by its own statement inside its own savepoint, so one broken source reads
`FAILED` and the others still list. A kind the caller may not read is `FORBIDDEN` and its
statement never runs. Nothing here writes, and nothing runs AQL: the comparison instant is an
input, not stored state.

Statement cost per request is constant: one per source, one freshness read for offenses, one
chip lookup for the page's anomalies. Each source returns its own top `offset + limit` rows in
the global order; they are merged here and cut once.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from sqlalchemy import ColumnElement, and_, case, exists, func, null, or_, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.alert import Alert
from app.models.catchall import CatchallSender
from app.models.enums import (
    AlertStatus,
    AnomalyFamily,
    CatchallReviewStatus,
    ClassificationReviewStatus,
    Severity,
    TriageStatus,
)
from app.models.fleet import LogSourceClassification
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.models.offense import OffenseSnapshot
from app.repositories.anomaly import AnomalyRepository
from app.repositories.offense import OffenseRepository
from app.schemas.messages import stored_message_parts
from app.schemas.queue import (
    CompareKind,
    QueueAlertChip,
    QueueChange,
    QueueFiltersApplied,
    QueueKind,
    QueueMarker,
    QueueOut,
    QueueRow,
    QueueScope,
    QueueTriageOut,
    SourceState,
    SourceStatus,
)
from app.schemas.related_activity import RelatedSectionState
from app.security.rbac import (
    PERM_ALERT_READ,
    PERM_ANOMALY_READ,
    PERM_FLEET_READ,
    PERM_OFFENSE_READ,
    Principal,
)
from app.services.queue_marker import get_marker
from app.services.related_activity import offense_collection_state

logger = logging.getLogger(__name__)

#: With no `since`, the last 24 hours count as new (design §2.3).
DEFAULT_WINDOW = timedelta(hours=24)
#: The deepest page the queue serves: it is unfinished work, not history.
MAX_DEPTH = 1000

#: The closed set of `SourceStatus.reason_code`.
REASON_LOAD_FAILED = "LOAD_FAILED"
REASON_NO_CONSOLE = "NO_CONSOLE"
REASON_COLLECTION_FAILING = "COLLECTION_FAILING"
REASON_COLLECTION_STALE = "COLLECTION_STALE"
REASON_CONSOLE_UNCOLLECTED = "CONSOLE_UNCOLLECTED"
REASON_OWNER_UNMAPPED = "OWNER_UNMAPPED"
REASON_ALERT_LINK_FAILED = "ALERT_LINK_FAILED"
REASON_ALERTS_NOT_READABLE = "ALERTS_NOT_READABLE"
REASON_NOT_APPLICABLE = "NOT_APPLICABLE_TO_FILTER"

#: Read permission per kind, checked per request rather than once at the router.
KIND_PERMISSION: dict[QueueKind, str] = {
    QueueKind.ANOMALY: PERM_ANOMALY_READ,
    QueueKind.ALERT: PERM_ALERT_READ,
    QueueKind.OFFENSE: PERM_OFFENSE_READ,
    QueueKind.SOURCE_REVIEW: PERM_FLEET_READ,
    QueueKind.CATCHALL_SENDER: PERM_FLEET_READ,
}
_KIND_ORDER = list(QueueKind)

_SEVERITY_BY_RANK: dict[int, Severity] = {
    5: Severity.CRITICAL,
    4: Severity.HIGH,
    3: Severity.MEDIUM,
    2: Severity.LOW,
    1: Severity.INFO,
}
_RANK_BY_SEVERITY: dict[str, int] = {s.value: r for r, s in _SEVERITY_BY_RANK.items()}

_FAMILY_BY_SUBJECT = {
    "LOG_SOURCE": AnomalyFamily.LOG_SOURCE,
    "WAF_ENDPOINT": AnomalyFamily.WAF,
    "IPS_ENTITY": AnomalyFamily.IPS,
    "IPS_SOURCE": AnomalyFamily.IPS,
    "PW_VALUE": AnomalyFamily.PROPERTY_WATCH,
}


def offense_severity_rank(magnitude: int | None) -> int:
    """The band of a QRadar magnitude: 9-10 CRITICAL, 7-8 HIGH, 5-6 MEDIUM, 3-4 LOW, else INFO.

    Rank 0 is "no severity": a missing magnitude is not INFO, and sorts after it.
    """
    if magnitude is None:
        return 0
    if magnitude >= 9:
        return 5
    if magnitude >= 7:
        return 4
    if magnitude >= 5:
        return 3
    if magnitude >= 3:
        return 2
    return 1


@dataclass
class _Entry:
    """One row with the key it sorts by in the merged queue."""

    key: tuple[Any, ...]
    row: QueueRow
    anomaly_id: str | None = None


@dataclass
class _Source:
    status: SourceStatus
    entries: list[_Entry] = field(default_factory=list)


def _sort_key(rank: int, waiting: datetime, kind: QueueKind, tiebreak: Any) -> tuple[Any, ...]:
    # The kind index keeps tiebreaks of different types from ever being compared.
    return (-rank, waiting, _KIND_ORDER.index(kind), tiebreak)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _age(as_of: datetime, since: datetime) -> int:
    return max(0, int((as_of - _aware(since)).total_seconds()))


def _change(value: Any) -> QueueChange | None:
    return QueueChange(value) if value is not None else None


def _status(
    kind: QueueKind,
    state: SourceState,
    *,
    count: int | None = None,
    new_count: int | None = None,
    permission: str | None = None,
    as_of: datetime | None = None,
    reason: str | None = None,
) -> SourceStatus:
    return SourceStatus(
        kind=kind,
        state=state,
        count=count,
        new_count=new_count,
        permission=permission,
        as_of=as_of,
        reason_code=reason,
    )


async def _guarded(
    session: AsyncSession, kind: QueueKind, work: Callable[[], Awaitable[_Source]]
) -> _Source:
    """Run one source in its own savepoint; a failure is `FAILED`, never an empty list."""
    try:
        async with session.begin_nested():
            return await work()
    except Exception:
        logger.exception("queue source %s failed to load", kind.value)
        return _Source(_status(kind, SourceState.FAILED, reason=REASON_LOAD_FAILED))


def _subject_label(row: dict[str, Any]) -> str | None:
    kind = row["subject_kind"]
    if kind == "WAF_ENDPOINT":
        return f"{row['endpoint_method']} {row['endpoint_normalized']}"
    if kind == "IPS_ENTITY":
        return row["entity_display_name"] or row["entity_value_key"]
    if kind == "PW_VALUE":
        return f"{row['pw_watch_name']}: {row['pw_value_key']}"
    return row["log_source_name"]


def _anomaly_entry(row: dict[str, Any], as_of: datetime) -> _Entry:
    family = _FAMILY_BY_SUBJECT[row["subject_kind"]]
    subject = _subject_label(row)
    stored = row.get("explanation_i18n")
    code, params = (
        stored_message_parts(stored.get("code"), stored.get("params"))
        if isinstance(stored, dict)
        else (None, None)
    )
    waiting = _aware(row["waiting_since"])
    change = _change(row["change"])
    detector = str(row["anomaly_type"])
    return _Entry(
        key=_sort_key(row["severity_rank"], waiting, QueueKind.ANOMALY, str(row["id"])),
        anomaly_id=str(row["id"]),
        row=QueueRow(
            kind=QueueKind.ANOMALY,
            family=family,
            id=str(row["id"]),
            title=row["explanation"] or (f"{detector} on {subject}" if subject else detector),
            title_code=code,
            title_params=params,
            subject=subject,
            detector=detector,
            severity=Severity(row["severity"]),
            magnitude=None,
            occurrence_count=None,
            state=str(row["state"]),
            # The left join reads a missing triage row as NEW, unowned, version 0.
            triage=QueueTriageOut(
                status=TriageStatus(row["triage_status"] or TriageStatus.NEW),
                version=row["triage_version"] or 0,
            ),
            owner=row["triage_owner"],
            owner_kind="app" if row["triage_owner"] else None,
            waiting_since=waiting,
            age_seconds=_age(as_of, waiting),
            new_since_last_visit=change is not None,
            change=change,
            alert=None,
            link=f"/anomalies/{row['id']}",
        ),
    )


async def _load_anomalies(
    session: AsyncSession,
    *,
    depth: int,
    subject: str,
    scope: QueueScope,
    compare_from: datetime,
    as_of: datetime,
    families: Collection[AnomalyFamily] | None,
    severities: Collection[Severity] | None,
    new_only: bool,
) -> _Source:
    rows, total, new = await AnomalyRepository(session).list_queue(
        limit=depth,
        subject=subject,
        scope=scope.value,
        compare_from=compare_from,
        families=families,
        severities=severities,
        new_only=new_only,
    )
    return _Source(
        _status(QueueKind.ANOMALY, SourceState.OK, count=total, new_count=new),
        [_anomaly_entry(r, as_of) for r in rows],
    )


async def _load_alerts(
    session: AsyncSession,
    *,
    depth: int,
    subject: str,
    scope: QueueScope,
    compare_from: datetime,
    as_of: datetime,
    severities: Collection[Severity] | None,
    new_only: bool,
) -> _Source:
    rank = case(_RANK_BY_SEVERITY, value=Alert.severity, else_=0)
    is_new = Alert.opened_at > compare_from
    is_updated = and_(Alert.acknowledged_at > compare_from, Alert.acknowledged_by != subject)
    change = case((is_new, "NEW"), (is_updated, "UPDATED"), else_=null())
    conditions: list[ColumnElement[bool]] = [
        Alert.status.in_([AlertStatus.OPEN.value, AlertStatus.ACKNOWLEDGED.value]),
        # An anomaly's alert is a chip on its anomaly row, not a second piece of work.
        func.jsonb_array_length(Alert.source_anomaly_ids) == 0,
    ]
    if severities is not None:
        conditions.append(Alert.severity.in_([s.value for s in severities]))
    if scope is QueueScope.MINE:
        conditions.append(Alert.acknowledged_by == subject)
    elif scope is QueueScope.UNASSIGNED:
        conditions.append(Alert.status == AlertStatus.OPEN.value)
    stmt = (
        select(
            Alert.id,
            Alert.title,
            Alert.title_i18n,
            Alert.severity,
            Alert.status,
            Alert.opened_at,
            Alert.acknowledged_by,
            Alert.occurrence_count,
            rank.label("severity_rank"),
            change.label("change"),
            func.count().over().label("queue_total"),
            func.count().filter(change.is_not(None)).over().label("queue_new"),
        )
        .where(*conditions)
        .order_by(rank.desc(), Alert.opened_at.asc(), Alert.id.asc())
        .limit(depth)
    )
    if new_only:
        stmt = stmt.where(change.is_not(None))
    rows = [dict(r._mapping) for r in (await session.execute(stmt)).all()]
    entries: list[_Entry] = []
    for r in rows:
        waiting = _aware(r["opened_at"])
        stored = r["title_i18n"]
        code, params = (
            stored_message_parts(stored.get("code"), stored.get("params"))
            if isinstance(stored, dict)
            else (None, None)
        )
        change_value = _change(r["change"])
        entries.append(
            _Entry(
                key=_sort_key(r["severity_rank"], waiting, QueueKind.ALERT, str(r["id"])),
                row=QueueRow(
                    kind=QueueKind.ALERT,
                    family=None,
                    id=str(r["id"]),
                    title=r["title"],
                    title_code=code,
                    title_params=params,
                    subject=None,
                    detector=None,
                    severity=Severity(r["severity"]),
                    magnitude=None,
                    occurrence_count=r["occurrence_count"],
                    state=str(r["status"]),
                    triage=None,
                    owner=r["acknowledged_by"],
                    owner_kind="app" if r["acknowledged_by"] else None,
                    waiting_since=waiting,
                    age_seconds=_age(as_of, waiting),
                    new_since_last_visit=change_value is not None,
                    change=change_value,
                    alert=None,
                    link=f"/alerts/{r['id']}",
                ),
            )
        )
    total = int(rows[0]["queue_total"]) if rows else 0
    new = int(rows[0]["queue_new"]) if rows else 0
    return _Source(
        _status(QueueKind.ALERT, SourceState.OK, count=total, new_count=new), entries
    )


async def _offense_freshness(
    session: AsyncSession, settings: Settings, now: datetime
) -> tuple[SourceState, datetime | None, str | None]:
    """`NOT_COLLECTED`, `STALE` or `OK` for offenses, from `collection_watermark`.

    One statement for every enabled console and for "any snapshot exists". The per-console
    rule is the one SOC-3a uses (`offense_collection_state`). With several consoles, one that
    never ran is reported as stale: the list is then missing that console's offenses.
    """
    has_snapshot = exists(select(OffenseSnapshot.qradar_offense_id).limit(1))
    rows = (
        await session.execute(
            select(
                QRadarInstance.id,
                CollectionWatermark.last_run_at,
                CollectionWatermark.consecutive_failures,
                has_snapshot.label("has_snapshot"),
            )
            .select_from(QRadarInstance)
            .outerjoin(
                CollectionWatermark,
                and_(
                    CollectionWatermark.instance_id == QRadarInstance.id,
                    CollectionWatermark.collector == "offense_snapshot",
                ),
            )
            .where(QRadarInstance.enabled.is_(True))
        )
    ).all()
    if not rows or not rows[0].has_snapshot:
        return SourceState.NOT_COLLECTED, None, REASON_NO_CONSOLE
    states = [
        offense_collection_state(
            last_run_at=r.last_run_at,
            consecutive_failures=r.consecutive_failures or 0,
            now=now,
            stale_after_seconds=settings.offense_stale_after_seconds,
        )
        for r in rows
    ]
    if all(s is RelatedSectionState.NOT_COLLECTED for s in states):
        return SourceState.NOT_COLLECTED, None, REASON_NO_CONSOLE
    runs = [_aware(r.last_run_at) for r in rows if r.last_run_at is not None]
    oldest = min(runs) if runs else None
    if any(r.consecutive_failures and r.consecutive_failures > 0 for r in rows):
        return SourceState.STALE, oldest, REASON_COLLECTION_FAILING
    if any(s is RelatedSectionState.STALE for s in states):
        return SourceState.STALE, oldest, REASON_COLLECTION_STALE
    if any(s is RelatedSectionState.NOT_COLLECTED for s in states):
        return SourceState.STALE, oldest, REASON_CONSOLE_UNCOLLECTED
    return SourceState.OK, oldest, None


async def _load_offenses(
    session: AsyncSession,
    settings: Settings,
    *,
    depth: int,
    principal: Principal,
    scope: QueueScope,
    compare_from: datetime,
    as_of: datetime,
    severities: Collection[Severity] | None,
    new_only: bool,
) -> _Source:
    state, fresh_at, reason = await _offense_freshness(session, settings, as_of)
    if state is SourceState.NOT_COLLECTED:
        return _Source(_status(QueueKind.OFFENSE, state, reason=reason))
    if scope is QueueScope.MINE and not principal.username:
        # The offense owner is a QRadar username. A principal without one cannot be matched,
        # which is not the same as having no offenses: the statement does not run.
        return _Source(
            _status(
                QueueKind.OFFENSE, SourceState.PARTIAL, reason=REASON_OWNER_UNMAPPED
            )
        )
    sub = OffenseRepository(session)._latest_subquery().alias("latest")
    m = sub.c.magnitude
    rank = case(
        (m.is_(None), 0), (m >= 9, 5), (m >= 7, 4), (m >= 5, 3), (m >= 3, 2), else_=1
    )
    waiting_col = func.coalesce(sub.c.start_time, sub.c.captured_at)
    is_new = sub.c.start_time > compare_from
    is_updated = sub.c.captured_at > compare_from
    change = case((is_new, "NEW"), (is_updated, "UPDATED"), else_=null())
    conditions: list[ColumnElement[bool]] = [sub.c.status == "OPEN"]
    if severities is not None:
        conditions.append(rank.in_([_RANK_BY_SEVERITY[s.value] for s in severities]))
    if scope is QueueScope.MINE:
        conditions.append(
            func.lower(sub.c.assigned_to) == (principal.username or "").lower()
        )
    elif scope is QueueScope.UNASSIGNED:
        conditions.append(sub.c.is_assigned.is_(False))
    stmt = (
        select(
            sub.c.instance_id,
            sub.c.qradar_offense_id,
            sub.c.description,
            sub.c.status,
            sub.c.magnitude,
            sub.c.assigned_to,
            waiting_col.label("waiting_since"),
            rank.label("severity_rank"),
            change.label("change"),
            func.count().over().label("queue_total"),
            func.count().filter(change.is_not(None)).over().label("queue_new"),
        )
        .where(*conditions)
        .order_by(rank.desc(), waiting_col.asc(), sub.c.instance_id, sub.c.qradar_offense_id)
        .limit(depth)
    )
    if new_only:
        stmt = stmt.where(change.is_not(None))
    rows = [dict(r._mapping) for r in (await session.execute(stmt)).all()]
    entries: list[_Entry] = []
    for r in rows:
        waiting = _aware(r["waiting_since"])
        change_value = _change(r["change"])
        offense_id = int(r["qradar_offense_id"])
        entries.append(
            _Entry(
                key=_sort_key(
                    r["severity_rank"],
                    waiting,
                    QueueKind.OFFENSE,
                    (str(r["instance_id"]), offense_id),
                ),
                row=QueueRow(
                    kind=QueueKind.OFFENSE,
                    family=None,
                    id=f"{r['instance_id']}:{offense_id}",
                    title=r["description"] or f"Offense {offense_id}",
                    title_code=None,
                    title_params=None,
                    subject=None,
                    detector=None,
                    severity=_SEVERITY_BY_RANK.get(r["severity_rank"]),
                    magnitude=r["magnitude"],
                    occurrence_count=None,
                    state=str(r["status"]),
                    triage=None,
                    owner=r["assigned_to"],
                    owner_kind="qradar" if r["assigned_to"] else None,
                    waiting_since=waiting,
                    age_seconds=_age(as_of, waiting),
                    new_since_last_visit=change_value is not None,
                    change=change_value,
                    alert=None,
                    link=f"/offenses/{offense_id}?instance_id={r['instance_id']}",
                ),
            )
        )
    total = int(rows[0]["queue_total"]) if rows else 0
    new = int(rows[0]["queue_new"]) if rows else 0
    return _Source(
        _status(
            QueueKind.OFFENSE,
            state,
            count=total,
            new_count=new,
            as_of=fresh_at if state is SourceState.STALE else None,
            reason=reason,
        ),
        entries,
    )


async def _load_source_review(
    session: AsyncSession,
    *,
    depth: int,
    compare_from: datetime,
    as_of: datetime,
    new_only: bool,
) -> _Source:
    review = LogSourceClassification.review_status
    is_new = LogSource.first_seen_at > compare_from
    change = case((is_new, "NEW"), else_=null())
    stmt = (
        select(
            LogSource.id,
            LogSource.name,
            LogSource.first_seen_at,
            review.label("review_status"),
            change.label("change"),
            func.count().over().label("queue_total"),
            func.count().filter(change.is_not(None)).over().label("queue_new"),
        )
        .select_from(LogSource)
        .outerjoin(
            LogSourceClassification, LogSourceClassification.log_source_id == LogSource.id
        )
        .where(
            or_(
                review == ClassificationReviewStatus.NEW.value,
                LogSourceClassification.log_source_id.is_(None),
            )
        )
        .order_by(LogSource.first_seen_at.asc(), LogSource.id.asc())
        .limit(depth)
    )
    if new_only:
        stmt = stmt.where(change.is_not(None))
    rows = [dict(r._mapping) for r in (await session.execute(stmt)).all()]
    entries: list[_Entry] = []
    for r in rows:
        waiting = _aware(r["first_seen_at"])
        change_value = _change(r["change"])
        entries.append(
            _Entry(
                key=_sort_key(0, waiting, QueueKind.SOURCE_REVIEW, str(r["id"])),
                row=QueueRow(
                    kind=QueueKind.SOURCE_REVIEW,
                    family=None,
                    id=str(r["id"]),
                    title=r["name"],
                    title_code=None,
                    title_params=None,
                    subject=None,
                    detector=None,
                    severity=None,
                    magnitude=None,
                    occurrence_count=None,
                    # No classification row reads as UNCLASSIFIED, as on the fleet page.
                    state=str(r["review_status"] or "UNCLASSIFIED"),
                    triage=None,
                    owner=None,
                    owner_kind=None,
                    waiting_since=waiting,
                    age_seconds=_age(as_of, waiting),
                    new_since_last_visit=change_value is not None,
                    change=change_value,
                    alert=None,
                    link=f"/fleet/sources?q={quote(r['name'], safe='')}",
                ),
            )
        )
    total = int(rows[0]["queue_total"]) if rows else 0
    new = int(rows[0]["queue_new"]) if rows else 0
    return _Source(
        _status(QueueKind.SOURCE_REVIEW, SourceState.OK, count=total, new_count=new),
        entries,
    )


async def _load_catchall(
    session: AsyncSession,
    *,
    depth: int,
    compare_from: datetime,
    as_of: datetime,
    new_only: bool,
) -> _Source:
    is_new = CatchallSender.first_seen_at > compare_from
    change = case((is_new, "NEW"), else_=null())
    stmt = (
        select(
            CatchallSender.id,
            CatchallSender.identifier,
            CatchallSender.review_status,
            CatchallSender.first_seen_at,
            change.label("change"),
            func.count().over().label("queue_total"),
            func.count().filter(change.is_not(None)).over().label("queue_new"),
        )
        .where(
            CatchallSender.review_status == CatchallReviewStatus.NEW.value,
            CatchallSender.active.is_(True),
        )
        .order_by(CatchallSender.first_seen_at.asc(), CatchallSender.id.asc())
        .limit(depth)
    )
    if new_only:
        stmt = stmt.where(change.is_not(None))
    rows = [dict(r._mapping) for r in (await session.execute(stmt)).all()]
    entries: list[_Entry] = []
    for r in rows:
        waiting = _aware(r["first_seen_at"])
        change_value = _change(r["change"])
        entries.append(
            _Entry(
                key=_sort_key(0, waiting, QueueKind.CATCHALL_SENDER, str(r["id"])),
                row=QueueRow(
                    kind=QueueKind.CATCHALL_SENDER,
                    family=None,
                    id=str(r["id"]),
                    title=r["identifier"],
                    title_code=None,
                    title_params=None,
                    subject=None,
                    detector=None,
                    severity=None,
                    magnitude=None,
                    occurrence_count=None,
                    state=str(r["review_status"]),
                    triage=None,
                    owner=None,
                    owner_kind=None,
                    waiting_since=waiting,
                    age_seconds=_age(as_of, waiting),
                    new_since_last_visit=change_value is not None,
                    change=change_value,
                    alert=None,
                    link="/fleet/catchall",
                ),
            )
        )
    total = int(rows[0]["queue_total"]) if rows else 0
    new = int(rows[0]["queue_new"]) if rows else 0
    return _Source(
        _status(QueueKind.CATCHALL_SENDER, SourceState.OK, count=total, new_count=new),
        entries,
    )


async def _alert_chips(
    session: AsyncSession, anomaly_ids: list[str]
) -> dict[str, QueueAlertChip]:
    """The open alert of each anomaly on the page: one statement for all of them.

    An alert can name several anomalies and an anomaly can have several unresolved alerts.
    Each anomaly gets one chip: an OPEN alert before an ACKNOWLEDGED one, then the newest.
    """
    rows = (
        await session.execute(
            select(Alert.id, Alert.status, Alert.opened_at, Alert.source_anomaly_ids)
            .where(
                Alert.status != AlertStatus.RESOLVED.value,
                Alert.source_anomaly_ids.has_any(array(anomaly_ids)),
            )
            .order_by(Alert.opened_at.desc(), Alert.id.desc())
        )
    ).all()
    wanted = set(anomaly_ids)
    best: dict[str, tuple[int, QueueAlertChip]] = {}
    for alert_id, status, _opened, named in rows:
        chip = QueueAlertChip(id=alert_id, status=AlertStatus(status))
        preference = 1 if status == AlertStatus.OPEN.value else 0
        for anomaly_id in named:
            if anomaly_id in wanted and (
                anomaly_id not in best or preference > best[anomaly_id][0]
            ):
                best[anomaly_id] = (preference, chip)
    return {key: chip for key, (_, chip) in best.items()}


async def build_queue(
    session: AsyncSession,
    settings: Settings,
    principal: Principal,
    *,
    scope: QueueScope,
    kinds: Collection[QueueKind],
    families: Collection[AnomalyFamily],
    severities: Collection[Severity],
    new_only: bool,
    since: datetime | None,
    limit: int,
    offset: int,
    now: datetime | None = None,
) -> QueueOut:
    """The queue page and one status per requested source. Writes nothing."""
    as_of = now or datetime.now(UTC)
    depth = offset + limit
    wanted_kinds = [k for k in _KIND_ORDER if k in set(kinds)] if kinds else list(_KIND_ORDER)
    # The stored marker is read only when some source is readable: with every kind forbidden
    # nothing is compared, so the page runs no statement at all.
    stored: datetime | None = None
    if any(principal.has(KIND_PERMISSION[k]) for k in wanted_kinds):
        stored = await get_marker(session, principal.subject)
    if since is not None:
        compare_from, compare_kind = since, CompareKind.OVERRIDE
    elif stored is not None:
        compare_from, compare_kind = stored, CompareKind.STORED
    else:
        compare_from, compare_kind = as_of - DEFAULT_WINDOW, CompareKind.DEFAULT_WINDOW
    family_filter = list(dict.fromkeys(families)) or None
    severity_filter = list(dict.fromkeys(severities)) or None
    subject = principal.subject

    sources: list[_Source] = []
    for kind in wanted_kinds:
        permission = KIND_PERMISSION[kind]
        if not principal.has(permission):
            sources.append(
                _Source(_status(kind, SourceState.FORBIDDEN, permission=permission))
            )
            continue
        excluded = (
            kind in (QueueKind.SOURCE_REVIEW, QueueKind.CATCHALL_SENDER)
            and (scope is QueueScope.MINE or severity_filter is not None)
        )
        if excluded:
            # Review items have no owner and no severity: the filter cannot select them.
            sources.append(
                _Source(
                    _status(
                        kind,
                        SourceState.OK,
                        count=0,
                        new_count=0,
                        reason=REASON_NOT_APPLICABLE,
                    )
                )
            )
            continue
        work: Callable[[], Awaitable[_Source]]
        if kind is QueueKind.ANOMALY:

            async def work() -> _Source:
                return await _load_anomalies(
                    session,
                    depth=depth,
                    subject=subject,
                    scope=scope,
                    compare_from=compare_from,
                    as_of=as_of,
                    families=family_filter,
                    severities=severity_filter,
                    new_only=new_only,
                )

        elif kind is QueueKind.ALERT:

            async def work() -> _Source:
                return await _load_alerts(
                    session,
                    depth=depth,
                    subject=subject,
                    scope=scope,
                    compare_from=compare_from,
                    as_of=as_of,
                    severities=severity_filter,
                    new_only=new_only,
                )

        elif kind is QueueKind.OFFENSE:

            async def work() -> _Source:
                return await _load_offenses(
                    session,
                    settings,
                    depth=depth,
                    principal=principal,
                    scope=scope,
                    compare_from=compare_from,
                    as_of=as_of,
                    severities=severity_filter,
                    new_only=new_only,
                )

        elif kind is QueueKind.SOURCE_REVIEW:

            async def work() -> _Source:
                return await _load_source_review(
                    session,
                    depth=depth,
                    compare_from=compare_from,
                    as_of=as_of,
                    new_only=new_only,
                )

        else:

            async def work() -> _Source:
                return await _load_catchall(
                    session,
                    depth=depth,
                    compare_from=compare_from,
                    as_of=as_of,
                    new_only=new_only,
                )

        sources.append(await _guarded(session, kind, work))

    merged = sorted((e for s in sources for e in s.entries), key=lambda e: e.key)
    page = merged[offset : offset + limit]

    # The alert chip on each anomaly row of the page, in one statement.
    anomaly_ids = [e.anomaly_id for e in page if e.anomaly_id is not None]
    anomaly_source = next((s for s in sources if s.status.kind is QueueKind.ANOMALY), None)
    if anomaly_source is not None and anomaly_source.status.state is SourceState.OK:
        if not principal.has(PERM_ALERT_READ):
            anomaly_source.status.state = SourceState.PARTIAL
            anomaly_source.status.reason_code = REASON_ALERTS_NOT_READABLE
        elif anomaly_ids:

            async def chips() -> _Source:
                found = await _alert_chips(session, anomaly_ids)
                for entry in page:
                    if entry.anomaly_id is not None:
                        entry.row.alert = found.get(entry.anomaly_id)
                return anomaly_source

            result = await _guarded(session, QueueKind.ANOMALY, chips)
            if result.status.state is SourceState.FAILED:
                # The anomaly rows are fine; only their alert chips are unknown.
                anomaly_source.status.state = SourceState.PARTIAL
                anomaly_source.status.reason_code = REASON_ALERT_LINK_FAILED

    statuses = [s.status for s in sources]
    counted = [
        s
        for s in statuses
        if s.count is not None and s.state is not SourceState.FORBIDDEN
    ]
    return QueueOut(
        as_of=as_of,
        marker=QueueMarker(
            compare_from=compare_from,
            kind=compare_kind,
            marked_at=stored,
        ),
        sources=statuses,
        total=sum(s.count or 0 for s in counted),
        total_exact=all(
            s.state is SourceState.FORBIDDEN or s.count is not None for s in statuses
        ),
        limit=limit,
        offset=offset,
        filters_applied=QueueFiltersApplied(
            scope=scope,
            kind=wanted_kinds,
            family=family_filter or [],
            severity=severity_filter or [],
            new_only=new_only,
            since=since,
        ),
        items=[e.row for e in page],
    )

