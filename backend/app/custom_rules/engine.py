"""DB-only evaluation of custom threshold rules (design §F.4).

One tick loads every rule, once, with its subject and its live breach. Each
subject kind that still has a rule allowed to judge then costs one metric
statement for the union of those subjects. The fold is pure. One flush writes
rule state, breach rows and the kind watermark.

The first time a kind has anything to judge, the watermark is seeded at the
latest closed bucket and nothing is judged, so enabling the feature never
replays history. Later ticks walk only buckets that collection has actually
written, plus the aligned gaps between them. When a kind has written nothing
for longer than its family's cadence, one bucket, the settle delay and the
scheduler's one-minute tolerance, every evaluable rule of that kind is
``STALE``. The watermark stays, so the backlog is judged when rows return.
``last_evaluated_at`` moves only when a bucket was judged.

Shadow mode (``custom_rules_alerting_enabled`` false, the default) writes
breaches and rule state and pages nothing. With it on, events go through
``AlertingCustomRuleSink``: one bulk alert write per tick, then a
notification per transition. An injected sink still receives the fold's
events, so a test double can observe them while shadow mode is on.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.alerts.dispatcher import AlertEnqueuer
from app.anomaly.seasonal import in_maintenance
from app.core.config import Settings
from app.custom_rules.alerts import (
    AlertingCustomRuleSink,
    CustomRuleAlertEvent,
    CustomRuleAlertSink,
    NoOpCustomRuleAlertSink,
    alerting_mode,
    extra_resolve_reason,
)
from app.custom_rules.catalog import catalog_entry
from app.custom_rules.evaluate import (
    BreachState,
    BucketFacts,
    FoldInput,
    FoldOutput,
    InterpretedReading,
    collection_silence,
    fold_rule,
    interpret,
    latest_closed_bucket,
    stale_bound_seconds,
    walk_buckets,
)
from app.custom_rules.loaders import (
    load_fleet_classes,
    load_ips_entities,
    load_ips_sources,
    load_log_sources,
    load_pw_values,
    load_waf_endpoints,
)
from app.models.custom_rule import CustomRule, CustomRuleBreach, CustomRuleWatermark
from app.models.enums import (
    DEFAULT_SEARCH_CADENCE,
    AnomalyState,
    CustomRuleMetric,
    CustomRuleSubjectKind,
    SearchFamily,
    WafMeasurement,
)
from app.models.ips import IpsEntity
from app.models.log_source import LogSource
from app.models.property_watch import PropertyWatch, PwValue
from app.models.search_cadence import SearchCadenceSetting
from app.models.waf import WafEndpoint

_RULE_COLUMNS = (
    "consecutive_breaching",
    "consecutive_under",
    "last_evaluated_bucket_start",
    "active_breach_id",
    "last_evaluated_at",
    "last_status",
    "last_status_reason",
)
_BREACH_COLUMNS = (
    "state",
    "first_bucket_start",
    "last_bucket_start",
    "opened_at",
    "recovering_at",
    "resolved_at",
    "worst_value",
    "worst_measurement",
    "last_value",
    "last_measurement",
    "unmeasured_buckets",
    "subject_label",
    "details",
)

_LOG_SOURCE = CustomRuleSubjectKind.LOG_SOURCE.value
_FLEET_CLASS = CustomRuleSubjectKind.FLEET_CLASS.value
_WAF_ENDPOINT = CustomRuleSubjectKind.WAF_ENDPOINT.value
_IPS_ENTITY = CustomRuleSubjectKind.IPS_ENTITY.value
_IPS_SOURCE = CustomRuleSubjectKind.IPS_SOURCE.value
_PW_VALUE = CustomRuleSubjectKind.PW_VALUE.value

# Fleet EPS is summed from Phase A ``log_source_metric`` rows, so it waits on
# the same search family as a log-source rule. There is no fleet cadence.
_KIND_FAMILY: dict[str, SearchFamily] = {
    _LOG_SOURCE: SearchFamily.PHASE_A_VOLUME,
    _FLEET_CLASS: SearchFamily.PHASE_A_VOLUME,
    _WAF_ENDPOINT: SearchFamily.WAF_COLLECTION,
    _IPS_ENTITY: SearchFamily.IPS_COLLECTION,
    _IPS_SOURCE: SearchFamily.IPS_COLLECTION,
    _PW_VALUE: SearchFamily.PROPERTY_WATCH,
}


@dataclass
class EvaluationReport:
    """What one tick did. ``disabled`` means the platform flag was off."""

    status: str
    opened: int = 0
    resolved: int = 0
    refreshed: int = 0
    seeded: list[str] = field(default_factory=list)
    judged_buckets: int = 0
    #: ``shadow`` or ``alerting``. F-3 reads ``alerting_mode`` for the same value.
    alerting_mode: str = "shadow"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "opened": self.opened,
            "resolved": self.resolved,
            "refreshed": self.refreshed,
            "seeded": list(self.seeded),
            "judged_buckets": self.judged_buckets,
            "alerting_mode": self.alerting_mode,
        }


@dataclass
class _Loaded:
    rule: CustomRule
    breach_row: CustomRuleBreach | None
    endpoint: WafEndpoint | None
    entity: IpsEntity | None
    source: LogSource | None
    pw_value: PwValue | None = None
    pw_watch: PropertyWatch | None = None


@dataclass
class _KindPlan:
    seeded: bool
    grid: list[datetime]
    facts: dict[str, dict[datetime, BucketFacts]]
    #: New ``evaluated_through`` when this tick should write the watermark.
    write_through: datetime | None
    #: Set when the kind has gone silent past its collection bound.
    stale_reason: str | None = None


class CustomRuleEngine:
    """Judge every custom rule once. Does not commit; the caller owns the transaction."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        sink: CustomRuleAlertSink | None = None,
        enqueuer: AlertEnqueuer | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        # An injected sink wins, including under shadow mode, so a test double
        # still sees fold events. The default follows the alerting setting.
        if sink is not None:
            self.sink = sink
        elif settings.custom_rules_alerting_enabled:
            self.sink = AlertingCustomRuleSink(session, enqueuer=enqueuer, clock=self._clock)
        else:
            self.sink = NoOpCustomRuleAlertSink()

    async def run(self) -> EvaluationReport:
        """One tick. A no-op, and no query, while custom rules are disabled."""
        mode = alerting_mode(self.settings.custom_rules_alerting_enabled)
        if not self.settings.custom_rules_enabled:
            return EvaluationReport(status="disabled", alerting_mode=mode)
        now = self._clock()
        interval = self.settings.collection_interval_seconds
        closed = latest_closed_bucket(now, interval)
        loaded = await self._load_rules()
        watermarks = await self._load_watermarks()
        plans = await self._plans(
            loaded, watermarks, closed=closed, interval=interval, now=now
        )
        outputs = [self._fold(item, plans, now) for item in loaded]
        self._persist(loaded, outputs, plans, watermarks, now)
        await self._dispatch(loaded, outputs)
        await self.session.flush()
        return _report(outputs, plans, mode)

    async def _load_rules(self) -> list[_Loaded]:
        """Every rule, its live breach and its subject, in one statement."""
        source_id = func.coalesce(
            CustomRule.log_source_id,
            WafEndpoint.log_source_id,
            IpsEntity.log_source_id,
        )
        rows = (
            await self.session.execute(
                select(
                    CustomRule,
                    CustomRuleBreach,
                    WafEndpoint,
                    IpsEntity,
                    LogSource,
                    PwValue,
                    PropertyWatch,
                )
                .outerjoin(
                    CustomRuleBreach,
                    CustomRuleBreach.id == CustomRule.active_breach_id,
                )
                .outerjoin(WafEndpoint, WafEndpoint.id == CustomRule.waf_endpoint_id)
                .outerjoin(IpsEntity, IpsEntity.id == CustomRule.ips_entity_id)
                .outerjoin(LogSource, LogSource.id == source_id)
                .outerjoin(PwValue, PwValue.id == CustomRule.pw_value_id)
                .outerjoin(PropertyWatch, PropertyWatch.id == PwValue.watch_id)
                .order_by(CustomRule.id)
            )
        ).all()
        loaded: list[_Loaded] = []
        for rule, breach, endpoint, entity, source, pw_value, pw_watch in rows:
            # A dangling pointer is not a breach. The tick clears it and does
            # not invent a resolution.
            breach_row: CustomRuleBreach | None = breach
            if rule.active_breach_id is None:
                breach_row = None
            loaded.append(
                _Loaded(
                    rule=rule,
                    breach_row=breach_row,
                    endpoint=endpoint,
                    entity=entity,
                    source=source,
                    pw_value=pw_value,
                    pw_watch=pw_watch,
                )
            )
        return loaded

    async def _load_watermarks(self) -> dict[str, CustomRuleWatermark]:
        rows = (await self.session.execute(select(CustomRuleWatermark))).scalars().all()
        return {_value(row.subject_kind): row for row in rows}

    async def _plans(
        self,
        loaded: list[_Loaded],
        watermarks: dict[str, CustomRuleWatermark],
        *,
        closed: datetime,
        interval: int,
        now: datetime,
    ) -> dict[str, _KindPlan]:
        by_kind: dict[str, list[_Loaded]] = {}
        for item in loaded:
            by_kind.setdefault(_value(item.rule.subject_kind), []).append(item)
        plans: dict[str, _KindPlan] = {}
        silent: list[tuple[str, datetime]] = []
        limit = self.settings.custom_rules_max_buckets_per_run
        for kind, items in by_kind.items():
            evaluable = [item for item in items if _evaluable(item, self.settings)]
            if not evaluable:
                # Disabled, retired or family-off rules are status updates only.
                # They must not seed a watermark or load a metric.
                continue
            current = watermarks.get(kind)
            if current is None:
                plans[kind] = _KindPlan(
                    seeded=True, grid=[], facts={}, write_through=closed
                )
                continue
            keys: list[str] = []
            seen: set[str] = set()
            for item in evaluable:
                key = _subject_key(item.rule)
                if key is None or key in seen:
                    continue
                seen.add(key)
                keys.append(key)
            facts = await self._load_metrics(
                kind, keys, after=current.evaluated_through, through=closed
            )
            observed = [
                bucket for per_subject in facts.values() for bucket in per_subject
            ]
            grid = walk_buckets(
                observed,
                watermark=current.evaluated_through,
                interval_seconds=interval,
                limit=limit,
                closed=closed,
            )
            plans[kind] = _KindPlan(
                seeded=False,
                grid=grid,
                facts=facts,
                # An empty grid does not move the watermark. A later tick
                # judges the backlog from the same point once rows exist.
                write_through=grid[-1] if grid else None,
            )
            if not grid:
                silent.append((kind, current.evaluated_through))
        if silent:
            cadences = await self._cadence_seconds([kind for kind, _watermark in silent])
            settle = self.settings.search_cadence_settle_seconds
            for kind, watermark in silent:
                plans[kind].stale_reason = collection_silence(
                    watermark=watermark,
                    closed=closed,
                    now=now,
                    bound_seconds=stale_bound_seconds(cadences[kind], settle, interval),
                )
        return plans

    async def _cadence_seconds(self, kinds: list[str]) -> dict[str, int]:
        """One read of the cadence rows these kinds wait on.

        A missing row is the migration seed, one hour, not the eight-hour
        ceiling. The evaluator does not insert the seed: that write belongs
        to the cadence service. ``create_all`` schemas have no seed, and the
        same default is what the seed would have written.
        """
        families = list(dict.fromkeys(_KIND_FAMILY[kind] for kind in kinds))
        rows = (
            await self.session.execute(
                select(
                    SearchCadenceSetting.family,
                    SearchCadenceSetting.cadence_seconds,
                ).where(
                    SearchCadenceSetting.family.in_([family.value for family in families])
                )
            )
        ).all()
        found = {str(family): int(seconds) for family, seconds in rows}
        default = int(DEFAULT_SEARCH_CADENCE)
        return {kind: found.get(_KIND_FAMILY[kind].value, default) for kind in kinds}

    async def _load_metrics(
        self,
        kind: str,
        keys: list[str],
        *,
        after: datetime,
        through: datetime,
    ) -> dict[str, dict[datetime, BucketFacts]]:
        """The one statement for this kind. An empty key list sends nothing."""
        if kind == _LOG_SOURCE:
            return await load_log_sources(
                self.session, _uuids(keys), after=after, through=through
            )
        if kind == _FLEET_CLASS:
            return await load_fleet_classes(
                self.session, keys, after=after, through=through
            )
        if kind == _WAF_ENDPOINT:
            return await load_waf_endpoints(
                self.session, _uuids(keys), after=after, through=through
            )
        if kind == _IPS_ENTITY:
            return await load_ips_entities(
                self.session, _uuids(keys), after=after, through=through
            )
        if kind == _IPS_SOURCE:
            return await load_ips_sources(
                self.session, _uuids(keys), after=after, through=through
            )
        if kind == _PW_VALUE:
            return await load_pw_values(
                self.session,
                _uuids(keys),
                after=after,
                through=through,
                coverage_floor=self.settings.pw_min_coverage,
            )
        raise RuntimeError(f"no bulk loader for subject kind {kind}")

    def _fold(self, item: _Loaded, plans: dict[str, _KindPlan], now: datetime) -> FoldOutput:
        rule = item.rule
        kind = _value(rule.subject_kind)
        retired, retired_reason = _retired(item)
        collected, family_reason = _family_collected(kind, self.settings)
        plan = plans.get(kind)
        readings: tuple[InterpretedReading, ...] = ()
        stale_reason = None
        if _evaluable(item, self.settings) and plan is not None and not plan.seeded:
            # A stale kind has an empty grid. Readings are not judged, and a
            # disabled or retired rule never takes the kind's stale status.
            stale_reason = plan.stale_reason
            if stale_reason is None:
                readings = tuple(self._readings(item, plan))
        breach = _breach_state(item.breach_row) if item.breach_row is not None else None
        return fold_rule(
            FoldInput(
                rule_id=rule.id,
                rule_name=rule.name,
                severity=_value(rule.severity),
                metric=CustomRuleMetric(_value(rule.metric)),
                operator=rule.operator,
                threshold_value=float(rule.threshold_value),
                sustain_buckets=rule.sustain_buckets,
                recovery_buckets=rule.recovery_buckets,
                consecutive_breaching=rule.consecutive_breaching,
                consecutive_under=rule.consecutive_under,
                last_evaluated_bucket_start=rule.last_evaluated_bucket_start,
                previous_status=rule.last_status,
                previous_reason=rule.last_status_reason,
                subject_kind=kind,
                subject_label=_label(item),
                platform_enabled=True,
                enabled=bool(rule.enabled),
                retired=retired,
                retired_reason=retired_reason,
                family_collected=collected,
                family_reason=family_reason,
                breach=breach,
                readings=readings,
                now=now,
                collection_stale_reason=stale_reason,
            )
        )

    def _readings(self, item: _Loaded, plan: _KindPlan) -> list[InterpretedReading]:
        key = _subject_key(item.rule)
        per_subject = plan.facts.get(key or "", {})
        metric = CustomRuleMetric(_value(item.rule.metric))
        readings: list[InterpretedReading] = []
        for bucket_start in plan.grid:
            facts = per_subject.get(bucket_start)
            if facts is None:
                facts = BucketFacts(bucket_start=bucket_start, present=False)
            reading = interpret(metric, facts)
            # Live maintenance as well as the flag collection stored on the bucket.
            # Either one pauses the rule; neither one is a verdict.
            if _in_live_maintenance(item, bucket_start):
                reading = InterpretedReading(
                    bucket_start=reading.bucket_start,
                    value=reading.value,
                    measurement=reading.measurement,
                    in_maintenance=True,
                    reason="the source is in maintenance",
                )
            readings.append(reading)
        return readings

    def _persist(
        self,
        loaded: list[_Loaded],
        outputs: list[FoldOutput],
        plans: dict[str, _KindPlan],
        watermarks: dict[str, CustomRuleWatermark],
        now: datetime,
    ) -> None:
        for item, output in zip(loaded, outputs, strict=True):
            rule = item.rule
            rule.consecutive_breaching = output.consecutive_breaching
            rule.consecutive_under = output.consecutive_under
            rule.last_evaluated_bucket_start = output.last_evaluated_bucket_start
            # Freshness is "a bucket was judged", not "the beat ran". A seed,
            # an idle gap and a stale kind leave the previous timestamp.
            if output.judged > 0:
                rule.last_evaluated_at = now
            if not output.preserve_status:
                rule.last_status = output.status
                rule.last_status_reason = output.status_reason
            rule.active_breach_id = None if output.breach is None else output.breach.id
            for column in _RULE_COLUMNS:
                flag_modified(rule, column)
            record = output.resolved if output.resolved is not None else output.breach
            if record is not None:
                self._save_breach(rule, record, item.breach_row)
        for kind, plan in plans.items():
            if plan.write_through is None:
                continue
            existing = watermarks.get(kind)
            if existing is None:
                self.session.add(
                    CustomRuleWatermark(
                        subject_kind=CustomRuleSubjectKind(kind),
                        evaluated_through=plan.write_through,
                        updated_at=now,
                    )
                )
                continue
            existing.evaluated_through = plan.write_through
            existing.updated_at = now
            flag_modified(existing, "evaluated_through")
            flag_modified(existing, "updated_at")

    def _save_breach(
        self,
        rule: CustomRule,
        record: BreachState,
        existing: CustomRuleBreach | None,
    ) -> None:
        if existing is not None and existing.id == record.id:
            existing.state = AnomalyState(record.state)
            existing.first_bucket_start = record.first_bucket_start
            existing.last_bucket_start = record.last_bucket_start
            existing.opened_at = record.opened_at
            existing.recovering_at = record.recovering_at
            existing.resolved_at = record.resolved_at
            existing.worst_value = record.worst_value
            existing.worst_measurement = _measurement(record.worst_measurement)
            existing.last_value = record.last_value
            existing.last_measurement = _measurement(record.last_measurement)
            existing.unmeasured_buckets = record.unmeasured_buckets
            existing.subject_label = record.subject_label
            existing.details = dict(record.details)
            for column in _BREACH_COLUMNS:
                flag_modified(existing, column)
            return
        self.session.add(
            CustomRuleBreach(
                id=record.id,
                rule_id=rule.id,
                state=AnomalyState(record.state),
                first_bucket_start=record.first_bucket_start,
                last_bucket_start=record.last_bucket_start,
                opened_at=record.opened_at,
                recovering_at=record.recovering_at,
                resolved_at=record.resolved_at,
                worst_value=record.worst_value,
                worst_measurement=_measurement(record.worst_measurement),
                last_value=record.last_value,
                last_measurement=_measurement(record.last_measurement),
                unmeasured_buckets=record.unmeasured_buckets,
                subject_label=record.subject_label,
                details=dict(record.details),
            )
        )

    async def _dispatch(self, loaded: list[_Loaded], outputs: list[FoldOutput]) -> None:
        """Hand events to the sink before flush, in the same transaction.

        Open and refresh share ``open_or_update``. Resolve is its own method.
        A sink error aborts the tick; this method does not swallow it.

        Shadow mode does not add resolves: a disabled rule, a retired subject
        and a recovery keep whatever alert they already have until paging is
        on. With paging on, those closes are one more event on the same sink,
        and ``flush`` writes the tick in one bulk call.
        """
        paging = self.settings.custom_rules_alerting_enabled
        alerting_sink = self.sink if isinstance(self.sink, AlertingCustomRuleSink) else None
        if paging and alerting_sink is not None:
            for item in loaded:
                alerting_sink.watch(item.rule.id)
        seen: set[uuid.UUID] = set()
        for item, output in zip(loaded, outputs, strict=True):
            for event in output.events:
                if event.action == "resolve":
                    seen.add(event.rule_id)
                    await self.sink.resolve(event)
                else:
                    await self.sink.open_or_update(event)
            reason = extra_resolve_reason(
                output.status,
                output.status_reason,
                breach_open=output.breach is not None,
                alerting_enabled=paging,
            )
            if reason is None or item.rule.id in seen:
                continue
            await self.sink.resolve(self._halt_event(item, reason))
            seen.add(item.rule.id)
        if paging and alerting_sink is not None:
            await alerting_sink.flush()

    def _halt_event(self, item: _Loaded, reason: str) -> CustomRuleAlertEvent:
        """A resolve the fold did not emit. The breach row is left as the fold wrote it."""
        rule = item.rule
        breach = item.breach_row
        now = self._clock()
        metric = CustomRuleMetric(_value(rule.metric))
        if breach is None:
            start = now
            end = now
            label = _label(item)
            breach_id = rule.id
            unmeasured = 0
            observed = None
            measurement = None
        else:
            start = breach.first_bucket_start
            end = breach.last_bucket_start
            label = breach.subject_label
            breach_id = breach.id
            unmeasured = breach.unmeasured_buckets
            observed = breach.last_value
            measurement = _value(breach.last_measurement) if breach.last_measurement else None
        return CustomRuleAlertEvent(
            action="resolve",
            rule_id=rule.id,
            breach_id=breach_id,
            rule_name=rule.name,
            severity=_value(rule.severity),
            subject_kind=_value(rule.subject_kind),
            subject_label=label,
            metric=metric.value,
            unit=catalog_entry(metric).unit,
            operator=rule.operator,
            threshold_value=float(rule.threshold_value),
            observed_value=observed,
            measurement=measurement,
            first_bucket_start=start,
            last_bucket_start=end,
            unmeasured_buckets=unmeasured,
            reason=reason,
        )


def _report(
    outputs: list[FoldOutput], plans: dict[str, _KindPlan], mode: str
) -> EvaluationReport:
    seeded = sorted(kind for kind, plan in plans.items() if plan.seeded)
    judged = sum(len(plan.grid) for plan in plans.values())
    status = "seeded" if seeded and judged == 0 else "ok"
    return EvaluationReport(
        status=status,
        opened=sum(1 for output in outputs for event in output.events if event.action == "open"),
        resolved=sum(
            1 for output in outputs for event in output.events if event.action == "resolve"
        ),
        refreshed=sum(
            1 for output in outputs for event in output.events if event.action == "refresh"
        ),
        seeded=seeded,
        judged_buckets=judged,
        alerting_mode=mode,
    )


def _evaluable(item: _Loaded, settings: Settings) -> bool:
    if not item.rule.enabled:
        return False
    retired, _reason = _retired(item)
    if retired:
        return False
    collected, _family_reason = _family_collected(_value(item.rule.subject_kind), settings)
    return collected


#: Kinds whose data comes from Phase A rows, which have no collection switch of their own.
_FAMILY_ALWAYS_COLLECTED = frozenset({_LOG_SOURCE, _FLEET_CLASS})


def _family_collected(kind: str, settings: Settings) -> tuple[bool, str | None]:
    if kind == _WAF_ENDPOINT:
        if settings.waf_collection_enabled:
            return True, None
        return False, "waf_collection_enabled is off"
    if kind in (_IPS_ENTITY, _IPS_SOURCE):
        if settings.ips_collection_enabled:
            return True, None
        return False, "ips_collection_enabled is off"
    if kind == _PW_VALUE:
        if settings.pw_collection_enabled:
            return True, None
        return False, "pw_collection_enabled is off"
    if kind in _FAMILY_ALWAYS_COLLECTED:
        return True, None
    # An unknown kind is never judged: the default must not be "collected".
    return False, f"no collection family is known for subject kind {kind}"


def _retired(item: _Loaded) -> tuple[bool, str | None]:
    kind = _value(item.rule.subject_kind)
    if kind == _FLEET_CLASS:
        return False, None
    if kind == _WAF_ENDPOINT:
        endpoint = item.endpoint
        if endpoint is None:
            return True, "the subject no longer exists"
        if not endpoint.is_active:
            if endpoint.retired_at is not None:
                return True, f"the endpoint was retired at {endpoint.retired_at.isoformat()}"
            return True, "the endpoint is retired"
        return False, None
    if kind == _IPS_ENTITY:
        entity = item.entity
        if entity is None:
            return True, "the subject no longer exists"
        if not entity.active:
            if entity.retired_at is not None:
                return True, f"the entity was retired at {entity.retired_at.isoformat()}"
            return True, "the entity is retired"
        return False, None
    if kind == _PW_VALUE:
        value = item.pw_value
        if value is None:
            return True, "the subject no longer exists"
        # A pinned value is never retired by idleness. One that is no longer pinned is a
        # value the rule may not depend on (design §G.10.2): fail closed.
        if not value.pinned:
            return True, "the value is no longer pinned"
        if not value.active:
            return True, "the value is retired"
        return False, None
    if kind in (_LOG_SOURCE, _IPS_SOURCE):
        if item.source is None:
            return True, "the subject no longer exists"
        return False, None
    # An unknown kind has no retirement branch: it is retired, never evaluated.
    return True, f"no retirement rule is known for subject kind {kind}"


def _label(item: _Loaded) -> str:
    kind = _value(item.rule.subject_kind)
    if kind == _FLEET_CLASS:
        return (item.rule.source_class or "")[:255]
    if kind == _WAF_ENDPOINT:
        if item.endpoint is None:
            return "retired endpoint"
        return item.endpoint.display_name[:255]
    if kind == _IPS_ENTITY:
        if item.entity is None:
            return "retired entity"
        shown = item.entity.display_name or item.entity.value_key
        return f"{_value(item.entity.dimension)} {shown}"[:255]
    if kind == _PW_VALUE:
        if item.pw_value is None or item.pw_watch is None:
            return "unknown property-watch value"
        shown = item.pw_value.value_key.replace("\x1f", " / ")
        return f"{item.pw_watch.name}: {shown}"[:255]
    if kind in (_LOG_SOURCE, _IPS_SOURCE):
        if item.source is None:
            return "unknown source"
        return item.source.name[:255]
    raise RuntimeError(f"no label for subject kind {kind}")


def _in_live_maintenance(item: _Loaded, bucket_start: datetime) -> bool:
    """The source's current maintenance window, for source-backed rules.

    A fleet class has no single window. WAF and IPS also carry the decision
    collection stored on the bucket; ``interpret`` already copied that onto
    the reading.
    """
    if _value(item.rule.subject_kind) == _FLEET_CLASS or item.source is None:
        return False
    return in_maintenance(
        bucket_start,
        maintenance_mode=bool(item.source.maintenance_mode),
        maintenance_until=item.source.maintenance_until,
    )


def _subject_key(rule: CustomRule) -> str | None:
    kind = _value(rule.subject_kind)
    if kind in (_LOG_SOURCE, _IPS_SOURCE):
        return None if rule.log_source_id is None else str(rule.log_source_id)
    if kind == _FLEET_CLASS:
        return rule.source_class
    if kind == _WAF_ENDPOINT:
        return None if rule.waf_endpoint_id is None else str(rule.waf_endpoint_id)
    if kind == _IPS_ENTITY:
        return None if rule.ips_entity_id is None else str(rule.ips_entity_id)
    if kind == _PW_VALUE:
        return None if rule.pw_value_id is None else str(rule.pw_value_id)
    return None


def _breach_state(row: CustomRuleBreach) -> BreachState:
    details = row.details if isinstance(row.details, dict) else {}
    return BreachState(
        id=row.id,
        state=_value(row.state),
        first_bucket_start=row.first_bucket_start,
        last_bucket_start=row.last_bucket_start,
        opened_at=row.opened_at,
        recovering_at=row.recovering_at,
        resolved_at=row.resolved_at,
        worst_value=row.worst_value,
        worst_measurement=_value(row.worst_measurement) if row.worst_measurement else None,
        last_value=row.last_value,
        last_measurement=_value(row.last_measurement) if row.last_measurement else None,
        unmeasured_buckets=row.unmeasured_buckets,
        subject_label=row.subject_label,
        details=dict(details),
        is_new=False,
    )


def _measurement(value: str | None) -> WafMeasurement | None:
    if value is None:
        return None
    return WafMeasurement(value)


def _uuids(keys: list[str]) -> list[uuid.UUID]:
    return [uuid.UUID(key) for key in keys]


def _value(value: object) -> str:
    raw = getattr(value, "value", None)
    if isinstance(raw, str):
        return raw
    return str(value)


__all__ = ["CustomRuleEngine", "EvaluationReport"]
