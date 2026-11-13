"""The SOC-17c daily digest: content, recipients, delivery and its run record.

Once per instance and UTC day, after ``DIGEST_HOUR_UTC``, the digest lists the
anomalies whose **final** two-layer verdict is ``DIGEST``: the outage policy has
not already made them record-only, and the alert policy has chosen the digest
over an alert. An incident under ``RECORD_ONLY`` is never listed.

The content is grouped by source then detector and carries only aggregates the
incident already stores: a count, the highest severity, the worst deviation
ratio, and the first and last abnormal bucket in UTC, with a link to the filtered
anomaly list. A source name is the only free text that leaves the database; no
raw event value, address or AQL appears in the mail.

Cost is fourteen statements per instance run, whatever the fleet and the number of
incidents: the per-instance advisory lock (take and release), one idempotency
check under that lock, one policy read, one outage-policy read, six grouped
incident reads (Phase A, WAF, IPS, property watch, rule, and identity), one
recipient read, one template read and one run insert. The source class comes
with the incident reads (an outer join), so it costs no statement of its own.
The rule and identity reads run even while their alerting switches are false;
those switches only drop the rows.

The run is recorded in ``alert_digest_run``. Its unique ``(instance_id,
digest_date)`` is the idempotency key: a second beat tick on the same day sends
nothing and writes nothing. Two ticks that overlap cannot both send: each
instance's "no row today -> build -> send -> insert" unit runs under the
collector advisory lock, and the row is committed before the lock is released,
so a second tick either skips at the lock or re-checks after it and finds the
row.

The window is fixed to the digest date: for date ``D`` it is
``[D-1 at DIGEST_HOUR_UTC, D at DIGEST_HOUR_UTC)`` in UTC, whatever time the
tick actually runs. A late worker neither shifts nor gaps it.

E-mail is optional and never faked: no configured relay records
``NOT_CONFIGURED``, an empty recipient union records ``NO_RECIPIENTS``, no
digest items record ``EMPTY``, and a delivery failure records ``FAILED`` with a
code from :data:`app.services.email_delivery.ERROR_CODES` and never the relay's
reply text. There is no webhook fallback.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from html import escape
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.notifiers.base import ResolvedTemplate
from app.alerts.policy import resolve as resolve_alert_policy
from app.alerts.routing import env_email_recipients
from app.core.config import Settings, get_settings
from app.fleet.outage_policy import resolve as resolve_outage
from app.models.alert_policy import AlertDigestRun, AlertPolicy
from app.models.email import EmailRecipient, EmailTemplate
from app.models.enums import (
    SEVERITY_BY_RANK,
    SEVERITY_RANK,
    AlertDigestState,
    AlertPolicyMode,
    AnomalyFamily,
    AnomalyType,
    OutagePolicyMode,
    Severity,
    SourceClass,
)
from app.models.fleet import LogSourceClassification
from app.models.identity_anomaly import IdentityAnomaly
from app.models.instance import QRadarInstance
from app.models.ips import IpsAnomaly
from app.models.log_source import LogSource, LogSourceAnomaly
from app.models.outage import OutageAlertPolicy
from app.models.property_watch import PropertyWatch, PwAnomaly
from app.models.rule import AnalyticsRule
from app.models.rule_anomaly import RuleAnomaly
from app.models.waf import WafAnomaly, WafEndpoint
from app.services.email_delivery import (
    EmailDeliveryError,
    MailParts,
    build_message,
    classify,
    valid_mailbox,
)
from app.services.email_delivery import (
    deliver as smtp_deliver,
)
from app.services.email_templates import render_template, shipped_digest_template
from app.services.locks import CollectorAdvisoryLock

#: The template key the digest owns. It never falls back to the default alert
#: template: an alert body would be sent with digest content.
DIGEST_TEMPLATE_TYPE = "digest"

#: The collector name of the per-instance digest lock. It keeps the digest from
#: contending with any collector's lock on the same instance.
DIGEST_LOCK_NAME = "alert_digest"

#: Rows listed before the "and N more sources" line (design §4.6).
DIGEST_TOP_ROWS = 50

#: A rank read back as its severity (DEBT-8). Rank 0 is "no severity at all",
#: which is an unknown stored value and not a `Severity` member; it stays at the
#: bottom and prints as INFO, as it did when the case spelled the ranks out.
_RANK_TO_SEVERITY: dict[int, Severity] = {**SEVERITY_BY_RANK, 0: Severity.INFO}


def _severity_rank(column: Any) -> Any:
    """A numeric rank so ``max()`` yields the highest severity, not the last string."""
    return case(SEVERITY_RANK, value=column, else_=0)


@dataclass(frozen=True)
class DigestItem:
    """One (source, detector) row of the digest."""

    family: AnomalyFamily
    source_id: uuid.UUID
    source_name: str
    source_class: str | None
    detector: str
    count: int
    severity: Severity
    worst_ratio: float | None
    first_bucket: datetime
    last_bucket: datetime

    def link(self, origin: str) -> str:
        """The filtered anomaly list for this row, on the site's own origin."""
        params: dict[str, str] = {"anomaly_type": self.detector}
        if self.family is AnomalyFamily.LOG_SOURCE:
            params["subject_kind"] = "LOG_SOURCE"
            params["log_source_id"] = str(self.source_id)
        elif self.family is AnomalyFamily.WAF:
            params["subject_kind"] = "WAF_ENDPOINT"
        elif self.family is AnomalyFamily.IPS:
            params["subject_kind"] = "IPS"
        elif self.family is AnomalyFamily.RULE:
            params["subject_kind"] = "RULE"
            params["rule_id"] = str(self.source_id)
        else:
            params["subject_kind"] = "PW_VALUE"
            params["property_watch_id"] = str(self.source_id)
        return f"{origin.rstrip('/')}/anomalies?{urlencode(params)}"


@dataclass(frozen=True)
class DigestRunResult:
    instance_id: uuid.UUID
    digest_date: date
    state: AlertDigestState
    item_count: int
    recipient_count: int
    error_code: str | None
    sent_at: datetime | None
    items: tuple[DigestItem, ...]
    #: Sources beyond the top 50 that the mail says are not listed.
    extra_sources: int


def _family_alerting(settings: Settings, family: AnomalyFamily) -> bool:
    """The family switch is the outer gate; a policy can only reduce paging.

    Phase A has no family switch. WAF, IPS, property watch, rule and identity are
    opt-in, so a digest row on a family that is still in shadow mode must list
    nothing. A new family must not fall through to Phase A's ``return True``.
    """
    if family is AnomalyFamily.WAF:
        return bool(settings.waf_alerting_enabled)
    if family is AnomalyFamily.IPS:
        return bool(settings.ips_alerting_enabled)
    if family is AnomalyFamily.PROPERTY_WATCH:
        return bool(settings.pw_alerting_enabled)
    if family is AnomalyFamily.RULE:
        return bool(settings.rule_alerting_enabled)
    if family is AnomalyFamily.IDENTITY:
        return bool(settings.identity_alerting_enabled)
    return True


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


async def recipient_addresses(session: AsyncSession) -> list[str]:
    """The union of ``NOTIFY_EMAIL_RECIPIENTS`` and every enabled stored recipient.

    The recipient's ``alert_types`` filter does not apply: the digest is a daily
    site summary, not a family-scoped alert. Addresses are lower-cased and
    de-duplicated, and an address that is not a plain mailbox is dropped before
    it can reach a header.
    """
    stored = (
        await session.scalars(
            select(EmailRecipient.address)
            .where(EmailRecipient.enabled.is_(True))
            .order_by(EmailRecipient.address)
        )
    ).all()
    seen: dict[str, str] = {}
    for address in [*stored, *env_email_recipients()]:
        text = address.strip().lower()
        if text and valid_mailbox(text):
            seen.setdefault(text, text)
    return sorted(seen)


def _choose_template(rows: Sequence[EmailTemplate]) -> ResolvedTemplate:
    """The enabled ``digest`` row, else the shipped digest template.

    Deliberately no fallback to the default alert template: its placeholders and
    wording describe one alert, not a daily summary.
    """
    for row in rows:
        if row.enabled and row.alert_type == DIGEST_TEMPLATE_TYPE:
            return ResolvedTemplate(
                subject=row.subject, html=row.html_body, text=row.text_body or ""
            )
    return shipped_digest_template()


def _items_table(items: Sequence[DigestItem], extra_sources: int, origin: str) -> str:
    rows = []
    for item in items:
        ratio = "—" if item.worst_ratio is None else f"{item.worst_ratio:.2f}x"
        rows.append(
            "<tr>"
            '<td style="padding: 6px 10px; border: 1px solid #d9dade">'
            f'<a href="{escape(item.link(origin), quote=True)}">{escape(item.source_name)}</a></td>'
            f'<td style="padding: 6px 10px; border: 1px solid #d9dade">{escape(item.detector)}</td>'
            '<td style="padding: 6px 10px; border: 1px solid #d9dade; text-align: right">'
            f"{item.count}</td>"
            f'<td style="padding: 6px 10px; border: 1px solid #d9dade">{item.severity.value}</td>'
            '<td style="padding: 6px 10px; border: 1px solid #d9dade; text-align: right">'
            f"{ratio}</td>"
            '<td style="padding: 6px 10px; border: 1px solid #d9dade">'
            f"{_utc(item.first_bucket)}</td>"
            '<td style="padding: 6px 10px; border: 1px solid #d9dade">'
            f"{_utc(item.last_bucket)}</td>"
            "</tr>"
        )
    table = (
        '<table style="border-collapse: collapse; font-family: Arial,Helvetica,sans-serif; '
        'font-size: 13px">'
        "<thead><tr>"
        '<th style="padding: 6px 10px; border: 1px solid #d9dade; text-align: left">Source</th>'
        '<th style="padding: 6px 10px; border: 1px solid #d9dade; text-align: left">Detector</th>'
        '<th style="padding: 6px 10px; border: 1px solid #d9dade; text-align: right">Incidents</th>'
        '<th style="padding: 6px 10px; border: 1px solid #d9dade; text-align: left">'
        "Highest severity</th>"
        '<th style="padding: 6px 10px; border: 1px solid #d9dade; text-align: right">'
        "Worst deviation</th>"
        '<th style="padding: 6px 10px; border: 1px solid #d9dade; text-align: left">'
        "First bucket (UTC)</th>"
        '<th style="padding: 6px 10px; border: 1px solid #d9dade; text-align: left">'
        "Last bucket (UTC)</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    if extra_sources:
        table += (
            '<p style="font-family: Arial,Helvetica,sans-serif; font-size: 13px">'
            f"and {extra_sources} more sources are not listed.</p>"
        )
    return table


def _items_text(items: Sequence[DigestItem], extra_sources: int, origin: str) -> str:
    lines = []
    for item in items:
        ratio = "not measured" if item.worst_ratio is None else f"{item.worst_ratio:.2f}x"
        lines.append(
            f"{item.source_name} | {item.detector} | {item.count} incidents | "
            f"{item.severity.value} | worst {ratio} | "
            f"{_utc(item.first_bucket)} to {_utc(item.last_bucket)} | "
            f"{item.link(origin)}"
        )
    if extra_sources:
        lines.append(f"and {extra_sources} more sources are not listed.")
    return "\n".join(lines)


class DigestService:
    """Build and record one instance's daily digest.

    ``deliver`` and ``clock`` are injectable so a test never opens a socket and
    never depends on the wall clock.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        deliver: Callable[[Settings, object], None] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._deliver = deliver or smtp_deliver

    async def _items(
        self,
        instance: QRadarInstance,
        *,
        start: datetime,
        end: datetime,
        policy_rows: Sequence[AlertPolicy],
        outage_rows: Sequence[OutageAlertPolicy],
    ) -> list[DigestItem]:
        """Run the six grouped reads and keep only final-DIGEST groups."""
        classification = func.coalesce(
            LogSourceClassification.source_class, SourceClass.UNCLASSIFIED.value
        )

        phase_a = (
            await self.session.execute(
                select(
                    LogSource.id,
                    LogSource.name,
                    classification,
                    LogSourceAnomaly.anomaly_type,
                    func.count(LogSourceAnomaly.id),
                    func.max(_severity_rank(LogSourceAnomaly.severity)),
                    func.max(LogSourceAnomaly.deviation_ratio),
                    func.min(
                        func.coalesce(
                            LogSourceAnomaly.anomaly_start, LogSourceAnomaly.detected_at
                        )
                    ),
                    func.max(
                        func.coalesce(
                            LogSourceAnomaly.anomaly_end, LogSourceAnomaly.detected_at
                        )
                    ),
                )
                .select_from(LogSourceAnomaly)
                .join(LogSource, LogSource.id == LogSourceAnomaly.log_source_id)
                .outerjoin(
                    LogSourceClassification,
                    LogSourceClassification.log_source_id == LogSource.id,
                )
                .where(
                    LogSource.instance_id == instance.id,
                    LogSourceAnomaly.suppressed.is_(False),
                    LogSourceAnomaly.detected_at >= start,
                    LogSourceAnomaly.detected_at < end,
                )
                .group_by(
                    LogSource.id,
                    LogSource.name,
                    LogSourceClassification.source_class,
                    LogSourceAnomaly.anomaly_type,
                )
            )
        ).all()

        waf = (
            await self.session.execute(
                select(
                    LogSource.id,
                    LogSource.name,
                    classification,
                    WafAnomaly.detector_type,
                    func.count(WafAnomaly.id),
                    func.max(_severity_rank(WafAnomaly.severity)),
                    func.max(WafAnomaly.deviation_ratio),
                    func.min(
                        func.coalesce(WafAnomaly.anomaly_start, WafAnomaly.detected_at)
                    ),
                    func.max(
                        func.coalesce(WafAnomaly.anomaly_end, WafAnomaly.detected_at)
                    ),
                )
                .select_from(WafAnomaly)
                .join(WafEndpoint, WafEndpoint.id == WafAnomaly.waf_endpoint_id)
                .join(LogSource, LogSource.id == WafEndpoint.log_source_id)
                .outerjoin(
                    LogSourceClassification,
                    LogSourceClassification.log_source_id == LogSource.id,
                )
                .where(
                    LogSource.instance_id == instance.id,
                    WafAnomaly.suppressed.is_(False),
                    WafAnomaly.detected_at >= start,
                    WafAnomaly.detected_at < end,
                )
                .group_by(
                    LogSource.id,
                    LogSource.name,
                    LogSourceClassification.source_class,
                    WafAnomaly.detector_type,
                )
            )
        ).all()

        ips = (
            await self.session.execute(
                select(
                    LogSource.id,
                    LogSource.name,
                    classification,
                    IpsAnomaly.detector_type,
                    func.count(IpsAnomaly.id),
                    func.max(_severity_rank(IpsAnomaly.severity)),
                    func.max(IpsAnomaly.deviation_ratio),
                    func.min(
                        func.coalesce(IpsAnomaly.anomaly_start, IpsAnomaly.detected_at)
                    ),
                    func.max(
                        func.coalesce(IpsAnomaly.anomaly_end, IpsAnomaly.detected_at)
                    ),
                )
                .select_from(IpsAnomaly)
                .join(LogSource, LogSource.id == IpsAnomaly.log_source_id)
                .outerjoin(
                    LogSourceClassification,
                    LogSourceClassification.log_source_id == LogSource.id,
                )
                .where(
                    LogSource.instance_id == instance.id,
                    IpsAnomaly.suppressed.is_(False),
                    IpsAnomaly.detected_at >= start,
                    IpsAnomaly.detected_at < end,
                )
                .group_by(
                    LogSource.id,
                    LogSource.name,
                    LogSourceClassification.source_class,
                    IpsAnomaly.detector_type,
                )
            )
        ).all()

        pw = (
            await self.session.execute(
                select(
                    PropertyWatch.id,
                    PropertyWatch.name,
                    PwAnomaly.detector_type,
                    func.count(PwAnomaly.id),
                    func.max(_severity_rank(PwAnomaly.severity)),
                    func.max(PwAnomaly.deviation_ratio),
                    func.min(
                        func.coalesce(PwAnomaly.anomaly_start, PwAnomaly.detected_at)
                    ),
                    func.max(
                        func.coalesce(PwAnomaly.anomaly_end, PwAnomaly.detected_at)
                    ),
                )
                .select_from(PwAnomaly)
                .join(PropertyWatch, PropertyWatch.id == PwAnomaly.watch_id)
                .where(
                    PropertyWatch.instance_id == instance.id,
                    PwAnomaly.suppressed.is_(False),
                    PwAnomaly.detected_at >= start,
                    PwAnomaly.detected_at < end,
                )
                .group_by(
                    PropertyWatch.id,
                    PropertyWatch.name,
                    PwAnomaly.detector_type,
                )
            )
        ).all()

        # Runs on every digest, including while rule_alerting_enabled is false.
        # Skipping it would make the pinned statement count depend on a default.
        rule = (
            await self.session.execute(
                select(
                    AnalyticsRule.id,
                    AnalyticsRule.name,
                    RuleAnomaly.detector_type,
                    func.count(RuleAnomaly.id),
                    func.max(_severity_rank(RuleAnomaly.severity)),
                    func.max(RuleAnomaly.deviation_ratio),
                    func.min(
                        func.coalesce(RuleAnomaly.anomaly_start, RuleAnomaly.detected_at)
                    ),
                    func.max(
                        func.coalesce(RuleAnomaly.anomaly_end, RuleAnomaly.detected_at)
                    ),
                )
                .select_from(RuleAnomaly)
                .join(AnalyticsRule, AnalyticsRule.id == RuleAnomaly.rule_id)
                .where(
                    AnalyticsRule.instance_id == instance.id,
                    RuleAnomaly.suppressed.is_(False),
                    RuleAnomaly.detected_at >= start,
                    RuleAnomaly.detected_at < end,
                )
                .group_by(
                    AnalyticsRule.id,
                    AnalyticsRule.name,
                    RuleAnomaly.detector_type,
                )
            )
        ).all()

        # Runs on every digest, including while identity_alerting_enabled is false.
        # Skipping it would make the pinned statement count depend on a default.
        identity = (
            await self.session.execute(
                select(
                    LogSource.id,
                    LogSource.name,
                    classification,
                    IdentityAnomaly.detector_type,
                    func.count(IdentityAnomaly.id),
                    func.max(_severity_rank(IdentityAnomaly.severity)),
                    func.max(IdentityAnomaly.deviation_ratio),
                    func.min(
                        func.coalesce(IdentityAnomaly.anomaly_start, IdentityAnomaly.detected_at)
                    ),
                    func.max(
                        func.coalesce(IdentityAnomaly.anomaly_end, IdentityAnomaly.detected_at)
                    ),
                )
                .select_from(IdentityAnomaly)
                .join(LogSource, LogSource.id == IdentityAnomaly.log_source_id)
                .outerjoin(
                    LogSourceClassification,
                    LogSourceClassification.log_source_id == LogSource.id,
                )
                .where(
                    LogSource.instance_id == instance.id,
                    IdentityAnomaly.suppressed.is_(False),
                    IdentityAnomaly.detected_at >= start,
                    IdentityAnomaly.detected_at < end,
                )
                .group_by(
                    LogSource.id,
                    LogSource.name,
                    LogSourceClassification.source_class,
                    IdentityAnomaly.detector_type,
                )
            )
        ).all()

        candidates: list[DigestItem] = []
        for family, rows in (
            (AnomalyFamily.LOG_SOURCE, phase_a),
            (AnomalyFamily.WAF, waf),
            (AnomalyFamily.IPS, ips),
        ):
            if not _family_alerting(self.settings, family):
                continue
            for (
                source_id,
                source_name,
                source_class,
                detector,
                count,
                severity_rank,
                worst_ratio,
                first_bucket,
                last_bucket,
            ) in rows:
                candidates.append(
                    DigestItem(
                        family=family,
                        source_id=source_id,
                        source_name=source_name,
                        source_class=str(source_class),
                        detector=str(detector),
                        count=int(count),
                        severity=_RANK_TO_SEVERITY[int(severity_rank)],
                        worst_ratio=float(worst_ratio) if worst_ratio is not None else None,
                        first_bucket=first_bucket,
                        last_bucket=last_bucket,
                    )
                )
        if _family_alerting(self.settings, AnomalyFamily.PROPERTY_WATCH):
            for (
                source_id,
                source_name,
                detector,
                count,
                severity_rank,
                worst_ratio,
                first_bucket,
                last_bucket,
            ) in pw:
                candidates.append(
                    DigestItem(
                        family=AnomalyFamily.PROPERTY_WATCH,
                        source_id=source_id,
                        source_name=source_name,
                        source_class=None,
                        detector=str(detector),
                        count=int(count),
                        severity=_RANK_TO_SEVERITY[int(severity_rank)],
                        worst_ratio=float(worst_ratio) if worst_ratio is not None else None,
                        first_bucket=first_bucket,
                        last_bucket=last_bucket,
                    )
                )
        if _family_alerting(self.settings, AnomalyFamily.RULE):
            for (
                source_id,
                source_name,
                detector,
                count,
                severity_rank,
                worst_ratio,
                first_bucket,
                last_bucket,
            ) in rule:
                candidates.append(
                    DigestItem(
                        family=AnomalyFamily.RULE,
                        source_id=source_id,
                        source_name=source_name,
                        source_class=None,
                        detector=str(detector),
                        count=int(count),
                        severity=_RANK_TO_SEVERITY[int(severity_rank)],
                        worst_ratio=float(worst_ratio) if worst_ratio is not None else None,
                        first_bucket=first_bucket,
                        last_bucket=last_bucket,
                    )
                )
        if _family_alerting(self.settings, AnomalyFamily.IDENTITY):
            for (
                source_id,
                source_name,
                source_class,
                detector,
                count,
                severity_rank,
                worst_ratio,
                first_bucket,
                last_bucket,
            ) in identity:
                candidates.append(
                    DigestItem(
                        family=AnomalyFamily.IDENTITY,
                        source_id=source_id,
                        source_name=source_name,
                        source_class=str(source_class) if source_class is not None else None,
                        detector=str(detector),
                        count=int(count),
                        severity=_RANK_TO_SEVERITY[int(severity_rank)],
                        worst_ratio=float(worst_ratio) if worst_ratio is not None else None,
                        first_bucket=first_bucket,
                        last_bucket=last_bucket,
                    )
                )

        items: list[DigestItem] = []
        for item in candidates:
            if (
                item.family is AnomalyFamily.LOG_SOURCE
                and item.detector == AnomalyType.NO_EVENTS.value
            ):
                outage = resolve_outage(
                    outage_rows,
                    log_source_id=item.source_id,
                    source_class=item.source_class,
                    instance_id=instance.id,
                    default=OutagePolicyMode(self.settings.outage_alert_default),
                )
                if outage.mode is OutagePolicyMode.RECORD_ONLY:
                    continue
            verdict = resolve_alert_policy(
                policy_rows,
                instance_id=instance.id,
                family=item.family,
                source_id=item.source_id,
                source_class=item.source_class,
                detector=item.detector,
                default=AlertPolicyMode(self.settings.alert_policy_default),
            )
            if verdict.mode is AlertPolicyMode.DIGEST:
                items.append(item)
        items.sort(key=lambda item: (-item.count, item.source_name.casefold(), item.detector))
        return items

    async def run_for_instance(
        self, instance: QRadarInstance
    ) -> DigestRunResult | None:
        """Build, send and record one instance's digest, at most once per date.

        Returns ``None`` when another tick holds this instance's lock or when
        today's row already exists. The "no row today -> build -> send -> insert"
        unit runs under the collector advisory lock, and the row is committed
        before the lock is released, so two overlapping ticks send one mail.

        Fourteen statements per run: the lock (take and release), one idempotency
        check, one policy read, one outage-policy read, six grouped incident
        reads (the sixth is the identity family), one recipient read, one template
        read and the run insert. Nothing here loops over a source or an incident.
        """
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        now = now.astimezone(UTC)
        digest_date = now.date()
        # The window is anchored to the digest date, not to the tick: a late
        # worker must neither shift nor gap it.
        end = datetime.combine(
            digest_date, time(hour=self.settings.digest_hour_utc), tzinfo=UTC
        )
        start = end - timedelta(days=1)

        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, DIGEST_LOCK_NAME
        ) as acquired:
            if not acquired:
                return None
            # The fleet-level check in `run_due_digests` is only a fast path; the
            # lock winner must look again, because the previous holder may have
            # committed between that read and this one.
            already = await self.session.scalar(
                select(AlertDigestRun.id).where(
                    AlertDigestRun.instance_id == instance.id,
                    AlertDigestRun.digest_date == digest_date,
                )
            )
            if already is not None:
                return None

            policy_rows = list((await self.session.scalars(select(AlertPolicy))).all())
            outage_rows = list(
                (await self.session.scalars(select(OutageAlertPolicy))).all()
            )
            items = await self._items(
                instance,
                start=start,
                end=end,
                policy_rows=policy_rows,
                outage_rows=outage_rows,
            )
            recipients = await recipient_addresses(self.session)
            template_rows = list(
                (
                    await self.session.scalars(
                        select(EmailTemplate).where(
                            EmailTemplate.alert_type == DIGEST_TEMPLATE_TYPE
                        )
                    )
                ).all()
            )

            shown = items[:DIGEST_TOP_ROWS]
            extra_sources = len(
                {item.source_id for item in items[DIGEST_TOP_ROWS:]}
            )
            template = _choose_template(template_rows)
            rendered = render_template(
                template,
                {
                    "instance.name": instance.name,
                    "site.name": self.settings.site_name,
                    "alert.type": "Daily digest",
                },
            )
            parts = MailParts(
                subject=rendered.subject,
                html=rendered.html
                + _items_table(shown, extra_sources, self.settings.public_origin),
                text=rendered.text
                + ("\n\n" if rendered.text else "")
                + _items_text(shown, extra_sources, self.settings.public_origin),
            )

            state = AlertDigestState.SUCCESS
            error_code: str | None = None
            sent_at: datetime | None = None
            if not self.settings.smtp_configured:
                state = AlertDigestState.NOT_CONFIGURED
            elif not recipients:
                state = AlertDigestState.NO_RECIPIENTS
            elif not items:
                state = AlertDigestState.EMPTY
            else:
                sent_at = now
                try:
                    message = build_message(self.settings, ", ".join(recipients), parts)
                    await asyncio.to_thread(self._deliver, self.settings, message)
                except Exception as exc:  # a code is the only safe record
                    state = AlertDigestState.FAILED
                    error_code = (
                        exc.code
                        if isinstance(exc, EmailDeliveryError)
                        else classify(exc).code
                    )

            self.session.add(
                AlertDigestRun(
                    instance_id=instance.id,
                    digest_date=digest_date,
                    state=state.value,
                    item_count=len(items),
                    recipient_count=len(recipients),
                    error_code=error_code,
                    sent_at=sent_at,
                )
            )
            await self.session.flush()
            # Commit while the lock is held: the next tick must see this row
            # before it can take the lock, or it would send a second mail.
            await self.session.commit()
            return DigestRunResult(
                instance_id=instance.id,
                digest_date=digest_date,
                state=state,
                item_count=len(items),
                recipient_count=len(recipients),
                error_code=error_code,
                sent_at=sent_at,
                items=tuple(items),
                extra_sources=extra_sources,
            )


async def run_due_digests(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    deliver: Callable[[Settings, object], None] | None = None,
) -> list[DigestRunResult]:
    """Run today's digest for every instance that is due and has no run row.

    Two statements for the fleet (the already-run instance ids and the instance
    list) plus fourteen per instance actually run. Before ``DIGEST_HOUR_UTC`` it
    returns without touching the database. The fleet check is a fast path only;
    each instance re-checks under its own advisory lock.
    """
    settings = settings or get_settings()
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    if moment.hour < settings.digest_hour_utc:
        return []
    today = moment.date()
    already = set(
        (
            await session.scalars(
                select(AlertDigestRun.instance_id).where(
                    AlertDigestRun.digest_date == today
                )
            )
        ).all()
    )
    instances = list(
        (
            await session.scalars(select(QRadarInstance).order_by(QRadarInstance.name))
        ).all()
    )
    service = DigestService(
        session, settings=settings, clock=lambda: moment, deliver=deliver
    )
    results: list[DigestRunResult] = []
    for instance in instances:
        if instance.id in already:
            continue
        result = await service.run_for_instance(instance)
        if result is not None:
            results.append(result)
    return results
