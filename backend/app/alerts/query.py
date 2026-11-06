"""The alert list's filter vocabulary and the SQL each filter means (SOC-7a).

The list and its facets are built from the same clauses. A facet count uses every
clause except the facet's own, so each chip measures what choosing it would leave;
the facets' `total` uses all of them and is the list's total. Every clause is a
predicate on one alert row -- the log-source clause is a set of `IN (SELECT ...)`
lookups into the family tables -- so the statement count stays the same whatever
the number of alerts.

`family` names the four anomaly families exactly as `GET /anomalies/summary`
names them (`log_source`, `waf`, `ips`, `property_watch`), and the four other
alert-only producers by their `alert.source_type`. It is deliberately not
`AnomalyType`: that enum is persisted in Phase A rows and in alert
fingerprints, and is left as it is (AGENTS.md rule 6). A row whose
`source_type` is not one of the eight known producers belongs to no family and
is therefore not returned by a family filter; the family mapping is exhaustive
over the producers the application opens alerts with.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, and_, case, func, literal_column, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.catchall import CatchallSender
from app.models.custom_rule import CustomRule
from app.models.enums import AlertStatus, Severity
from app.models.ips import IpsEntity
from app.models.property_watch import PropertyWatch, PwValue
from app.models.waf import WafEndpoint


class AlertFamily(StrEnum):
    """The closed set of families that open alerts.

    The first four are the anomaly families (`log_source`, `waf`, `ips`,
    `property_watch`); the rest are producers that raise alerts but are not part
    of the anomaly lifecycle. Every member is named after what the alert is
    about, never after a specific detector type.
    """

    LOG_SOURCE = "log_source"
    WAF = "waf"
    IPS = "ips"
    PROPERTY_WATCH = "property_watch"
    CATCHALL_SENDER = "catchall_sender"
    CUSTOM_RULE = "custom_rule"
    SCHEDULED_SEARCH = "scheduled_search"
    QRADAR_INSTANCE = "qradar_instance"


#: Every `alert.source_type` the application opens alerts with, mapped onto the
#: family an analyst filters by. `ips_entity` and `ips_source` are one family:
#: both are IPS anomalies. The keys are exactly `EMAIL_ALERT_TYPES`.
FAMILY_BY_SOURCE_TYPE: dict[str, AlertFamily] = {
    "log_source": AlertFamily.LOG_SOURCE,
    "waf_endpoint": AlertFamily.WAF,
    "ips_entity": AlertFamily.IPS,
    "ips_source": AlertFamily.IPS,
    "pw_value": AlertFamily.PROPERTY_WATCH,
    "catchall_sender": AlertFamily.CATCHALL_SENDER,
    "custom_rule": AlertFamily.CUSTOM_RULE,
    "scheduled_search": AlertFamily.SCHEDULED_SEARCH,
    "qradar_instance": AlertFamily.QRADAR_INSTANCE,
}


def alert_family(source_type: str) -> AlertFamily | None:
    """The family of one `alert.source_type`, or None for an unknown producer."""
    return FAMILY_BY_SOURCE_TYPE.get(source_type)


#: The SQL twin of `FAMILY_BY_SOURCE_TYPE`, for the filter and the facade counts.
FAMILY_EXPRESSION = case(FAMILY_BY_SOURCE_TYPE, value=Alert.source_type)

#: Severity as an ordinal, so `sort=severity&order=asc` lists INFO first rather
#: than sorting the labels alphabetically (which would put CRITICAL first).
_SEVERITY_RANK = {
    Severity.INFO.value: 0,
    Severity.LOW.value: 1,
    Severity.MEDIUM.value: 2,
    Severity.HIGH.value: 3,
    Severity.CRITICAL.value: 4,
}
SEVERITY_RANK_EXPRESSION = case(_SEVERITY_RANK, value=Alert.severity)


class AlertSort(StrEnum):
    """The columns an alert list may be ordered by."""

    #: `opened_at`: the alert's creation time, not the database row's insert time.
    CREATED_AT = "created_at"
    SEVERITY = "severity"
    UPDATED_AT = "updated_at"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


#: The longest `q` the route accepts, and so the most a title search scans.
MAX_Q_LENGTH = 100


def escape_like(value: str) -> str:
    """Neutralise LIKE metacharacters so `q` is a literal substring.

    Without this, `q=%` would match every alert and `q=_` would match any
    single character -- a filter that silently means something else.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def normalise_q(value: str | None) -> str | None:
    """The title search as it is applied: stripped, or None when nothing was asked.

    A blank search is refused rather than treated as "no filter": a caller that
    sent one asked a question, and answering with every row hides the mistake.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise ValueError("q must not be blank")
    if len(text) > MAX_Q_LENGTH:
        raise ValueError(f"q must be at most {MAX_Q_LENGTH} characters")
    return text


@dataclass(frozen=True)
class AlertFilters:
    """One request's filters, already validated.

    The list and the facets' `total` read these clauses unchanged; a facet count
    reads a copy with only its own filter dropped (`facet_counts`).
    """

    status: AlertStatus | None = None
    severities: tuple[Severity, ...] = ()
    log_source_ids: tuple[uuid.UUID, ...] = ()
    family: AlertFamily | None = None
    since: datetime | None = None
    until: datetime | None = None
    title_contains: str | None = None

    def clauses(self) -> list[ColumnElement[bool]]:
        """The WHERE clauses: one predicate per filter, combined with AND."""
        clauses: list[ColumnElement[bool]] = []
        if self.status is not None:
            clauses.append(Alert.status == self.status.value)
        if self.severities:
            clauses.append(Alert.severity.in_([s.value for s in self.severities]))
        if self.log_source_ids:
            clauses.append(_log_source_clause(self.log_source_ids))
        if self.family is not None:
            clauses.append(FAMILY_EXPRESSION.in_([self.family.value]))
        if self.since is not None:
            clauses.append(Alert.opened_at >= self.since)
        if self.until is not None:
            # Half-open at the top, so a caller can page day by day without
            # counting the boundary twice.
            clauses.append(Alert.opened_at < self.until)
        if self.title_contains is not None:
            clauses.append(
                Alert.title.ilike(f"%{escape_like(self.title_contains)}%", escape="\\")
            )
        return clauses

    def order_by(self, sort: AlertSort, order: SortOrder) -> list[Any]:
        """The ORDER BY list, always total so a page boundary cannot repeat a row."""
        descending = order is SortOrder.DESC
        if sort is AlertSort.SEVERITY:
            # Most severe first is the useful default for a high-to-low read;
            # `asc` deliberately means INFO first, not the label's alphabet.
            primary: Any = (
                SEVERITY_RANK_EXPRESSION.desc() if descending else SEVERITY_RANK_EXPRESSION.asc()
            )
            return [primary, Alert.opened_at.desc(), Alert.id.desc()]
        if sort is AlertSort.UPDATED_AT:
            return [
                Alert.updated_at.desc() if descending else Alert.updated_at.asc(),
                Alert.id.desc() if descending else Alert.id.asc(),
            ]
        return [
            Alert.opened_at.desc() if descending else Alert.opened_at.asc(),
            Alert.id.desc() if descending else Alert.id.asc(),
        ]


def _log_source_clause(log_source_ids: tuple[uuid.UUID, ...]) -> ColumnElement[bool]:
    """Alerts whose subject belongs to one of these log sources.

    The link is stored differently per family, so this is one clause with a
    branch per family rather than one column comparison:

    * `log_source` and `ips_source` alerts already hold the log source id in
      `source_id`;
    * a WAF, IPS-entity, catch-all-sender or pinned-value alert holds its own
      subject's id, which is resolved to the log source here;
    * a custom-rule alert follows its rule's subject.

    An alert with no single log source -- a type- or group-scoped property
    watch, a fleet-class custom rule, a scheduled search, the offense
    collection -- is about no one source and is not matched by a source
    filter. That is narrower than "every alert that mentions the source",
    never wider: a source filter never shows a row from another source.
    """
    ids = list(log_source_ids)
    endpoint_ids = select(WafEndpoint.id).where(WafEndpoint.log_source_id.in_(ids))
    entity_ids = select(IpsEntity.id).where(IpsEntity.log_source_id.in_(ids))
    value_ids = (
        select(PwValue.id)
        .join(PropertyWatch, PropertyWatch.id == PwValue.watch_id)
        .where(PropertyWatch.log_source_id.in_(ids))
    )
    return or_(
        and_(
            Alert.source_type.in_(("log_source", "ips_source")),
            Alert.source_id.in_(ids),
        ),
        and_(Alert.source_type == "waf_endpoint", Alert.source_id.in_(endpoint_ids)),
        and_(Alert.source_type == "ips_entity", Alert.source_id.in_(entity_ids)),
        and_(
            Alert.source_type == "catchall_sender",
            Alert.source_id.in_(
                select(CatchallSender.id).where(
                    CatchallSender.catchall_log_source_id.in_(ids)
                )
            ),
        ),
        and_(Alert.source_type == "pw_value", Alert.source_id.in_(value_ids)),
        and_(
            Alert.source_type == "custom_rule",
            Alert.source_id.in_(
                select(CustomRule.id).where(
                    or_(
                        CustomRule.log_source_id.in_(ids),
                        CustomRule.waf_endpoint_id.in_(endpoint_ids),
                        CustomRule.ips_entity_id.in_(entity_ids),
                        CustomRule.pw_value_id.in_(value_ids),
                    )
                )
            ),
        ),
    )


async def facet_counts(
    session: AsyncSession, filters: AlertFilters
) -> tuple[dict[str, int], dict[str, int], dict[str, int], int]:
    """Counts per severity, family and status, plus the list's own total.

    Standard faceted search: each facet is counted with every filter except its
    own, so a chip answers "how many alerts would this value add or leave"
    rather than "how many already pass the filter this chip would replace". On
    `?severity=HIGH` the MEDIUM chip therefore counts the MEDIUM alerts the
    other filters leave, where counting with the severity filter too would draw
    a false zero. `family` and `status` follow the same rule.

    `total` is the exception: it applies every filter, so it is exactly what
    `GET /alerts` reports for the same query. A facet's counts add up to it only
    when that facet's own filter is unset.

    All four figures are one `union_all` statement, never a read per alert or
    per facet, so the statement count does not grow with the population.
    """
    clauses = filters.clauses()

    def facet(name: str, key: Any, without_own: AlertFilters) -> Any:
        return (
            select(
                literal_column(f"'{name}'").label("facet"),
                key.label("key"),
                func.count().label("count"),
            )
            .where(*without_own.clauses())
            .group_by(key)
        )

    rows = await session.execute(
        union_all(
            facet("severity", Alert.severity, replace(filters, severities=())),
            facet("family", FAMILY_EXPRESSION, replace(filters, family=None)),
            facet("status", Alert.status, replace(filters, status=None)),
            # The fourth branch is not grouped: one row carries the list's total.
            select(
                literal_column("'total'").label("facet"),
                literal_column("'all'").label("key"),
                func.count().label("count"),
            ).where(*clauses),
        )
    )
    severity: dict[str, int] = {}
    family: dict[str, int] = {}
    status: dict[str, int] = {}
    total = 0
    target = {"severity": severity, "family": family, "status": status}
    for name, key, count in rows.all():
        if str(name) == "total":
            total = int(count)
        elif key is not None:
            target[str(name)][str(key)] = int(count)
    return severity, family, status, total
