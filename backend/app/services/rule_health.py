"""Rule-health classification.

The question this answers is "is this detection actually working", and the
reason it is more than a boolean is that silence has several very different
causes. The classifier is deliberately explicit and ordered, because the wrong
precedence produces actively misleading answers:

  1. **DISABLED** — an intentionally disabled rule is not broken. Reporting it as
     inactive would bury the genuinely broken rules under noise.
  2. **INSUFFICIENT_DATA** — a rule inside its grace period, or with too little
     observed history, has not had a fair chance to fire. Calling a two-day-old
     detection "inactive" is how teams learn to ignore this page.
  3. **DEPENDENCY_DEGRADED** — if the telemetry the rule needs is not arriving,
     the rule's silence says nothing about the rule. This is the distinction
     between "never fired" and "could not have fired", and it is the single most
     important judgement here.
  4. **NOISY** — firing far above expectation.
  5. **NEVER_OBSERVED** / **INACTIVE** — genuinely silent, past its grace period,
     with healthy dependencies. NEVER_OBSERVED and INACTIVE are separate because
     "this has never worked" and "this stopped working" get different responses.
  6. **HEALTHY**.

Measured rule matches (Phase E.1a) add evidence per rule type, via
`app.rules.matches.match_evidence`. An EVENT rule is judged on its measured match
count, and its silence is provable only when every window of the evaluation span
is COMPLETE. A COMMON rule's measured matches are a lower bound: they prove firing
and never silence. Every other type is judged exactly as before.

Zero offenses is not zero detections: `trigger_count` and
`offense_contribution_count` are evaluated separately, so a rule that fires
normally but is configured to create no offense reads HEALTHY rather than dead.

Verdicts are versioned (`logic_version`) and stored as snapshots, so changing
this logic never silently rewrites the meaning of history. Flapping is damped by
requiring consecutive agreeing evaluations before the stored verdict changes.

This module never enables, disables or edits a rule. Classification is
observation only.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.enums import RuleDependencyKind, RuleHealthStatus
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.models.rule import (
    AnalyticsRule,
    RuleDependency,
    RuleHealthSnapshot,
    RuleMatchMetric,
    RuleMatchWindow,
    RuleMetric,
)
from app.rules.matches import (
    CoverageState,
    MatchCoverage,
    MatchEvidence,
    assess_coverage,
    floor_to_window,
    match_evidence,
)

logger = logging.getLogger("app.services.rule_health")

#: Watermark whose existence proves rule-firing observations are complete.
#: `RuleMetric` rows are the only evidence that anyone *looked* for firings, so
#: until this collector has completed a run covering the evaluation window,
#: "no observed firings" means "nobody has looked", not "it never fired".
RULE_METRIC_COLLECTOR = "rule_metric"


@dataclass
class HealthVerdict:
    """A classification plus everything needed to justify it later."""

    status: RuleHealthStatus
    reason: str
    confidence: float = 1.0
    window_start: datetime | None = None
    window_end: datetime | None = None
    last_triggered_at: datetime | None = None
    trigger_count: int = 0
    offense_contribution_count: int = 0
    expected_daily_firings: float | None = None
    enabled: bool = True
    building_blocks_healthy: bool | None = None
    required_log_sources_healthy: bool | None = None
    missing_dependencies: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MeasuredMatches:
    """Measured rule-match evidence for one rule over the evaluation window."""

    evidence: MatchEvidence
    coverage: MatchCoverage
    #: Sum of `matched_events` over the span. Positive evidence whatever the
    #: coverage: a stored row is a match that happened.
    events: int = 0
    last_match_at: datetime | None = None


@dataclass
class RuleHealthReport:
    instance_id: uuid.UUID
    evaluated: int = 0
    snapshots_written: int = 0
    changed: int = 0
    held_for_flap: int = 0
    by_status: dict[str, int] = field(default_factory=dict)


class RuleHealthEvaluator:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def logic_version(self) -> int:
        return self.settings.rule_health_logic_version

    # ------------------------------------------------------------ evaluation
    async def evaluate_instance(self, instance: QRadarInstance) -> RuleHealthReport:
        now = self._clock()
        report = RuleHealthReport(instance_id=instance.id)

        rules = list(
            (
                await self.session.scalars(
                    select(AnalyticsRule).where(
                        AnalyticsRule.instance_id == instance.id,
                        # Building blocks are dependencies, not detections; they
                        # are judged through the rules that use them.
                        AnalyticsRule.is_building_block.is_(False),
                    )
                )
            ).all()
        )
        if not rules:
            return report

        # Pre-load everything the loop needs, so N rules cost a constant number
        # of queries rather than N.
        window_start = now - timedelta(days=self.settings.rule_health_window_days)
        firing = await self._firing_counts([r.id for r in rules], window_start)
        deps = await self._dependencies([r.id for r in rules])
        bb_state = await self._building_block_state(instance.id)
        ls_health = await self._log_source_type_health(instance.id, now)
        observation_status = await self._observation_status(instance.id, window_start)
        observation_complete = observation_status == "complete"
        coverage = await self._match_coverage(instance.id, window_start, now)
        match_sums = (
            await self._match_sums([r.id for r in rules], window_start)
            if coverage.state != CoverageState.NONE
            else {}
        )

        for rule in rules:
            evidence = match_evidence(rule.rule_type)
            events, last_match = match_sums.get(rule.id, (0, None))
            verdict = self.classify(
                rule,
                now=now,
                window_start=window_start,
                trigger_count=firing.get(rule.id, (0, 0, False))[0],
                offense_count=firing.get(rule.id, (0, 0, False))[1],
                metric_inferred=firing.get(rule.id, (0, 0, False))[2],
                dependencies=deps.get(rule.id, []),
                building_block_enabled=bb_state,
                log_source_type_healthy=ls_health,
                observation_complete=observation_complete,
                observation_status=observation_status,
                matches=(
                    MeasuredMatches(evidence, coverage, events, last_match)
                    if coverage.state != CoverageState.NONE
                    else None
                ),
            )
            report.evaluated += 1
            report.by_status[verdict.status] = report.by_status.get(verdict.status, 0) + 1

            written = await self._persist(rule, verdict, now=now)
            if written:
                report.snapshots_written += 1

            if await self._apply_with_flap_damping(rule, verdict, now=now):
                report.changed += 1
            elif rule.health_status != verdict.status:
                report.held_for_flap += 1

        await self.session.flush()
        return report

    def classify(
        self,
        rule: AnalyticsRule,
        *,
        now: datetime,
        window_start: datetime,
        trigger_count: int,
        offense_count: int,
        dependencies: list[RuleDependency],
        building_block_enabled: dict[str, bool],
        log_source_type_healthy: dict[str, bool],
        observation_complete: bool = False,
        observation_status: str = "never_run",
        metric_inferred: bool = False,
        matches: MeasuredMatches | None = None,
    ) -> HealthVerdict:
        """Classify one rule.

        `observation_complete` says whether a rule-metric collection has actually
        covered the window. It defaults to False — fail closed — because without
        it a zero trigger count carries no information, and NEVER_OBSERVED would
        be an assertion the platform cannot support.

        `matches` carries measured rule-match evidence and is None when the
        match collector has never run, which leaves every verdict as it was.
        What it adds depends on the rule's type: an EVENT rule's measured count
        replaces the inferred one, and its silence is provable only with
        COMPLETE coverage; a COMMON rule's count is a lower bound that can prove
        firing but never silence; any other type ignores it. Incomplete
        coverage of an EVENT rule overrides `observation_complete` to False, so
        the measured path can only ever be stricter than the old one.
        """
        expected = rule.expected_daily_firings
        last_fired = rule.last_fired_at
        measurement: dict = {}
        measured_exact = False  # a measured count over a fully observed span
        measured_lower_bound = False  # matches were the figure that set the count
        if (
            matches is not None
            and matches.evidence != MatchEvidence.NOT_MEASURED
            and matches.coverage.state != CoverageState.NONE
        ):
            measurement = {
                "match_evidence": str(matches.evidence),
                "match_coverage": str(matches.coverage.state),
                "match_windows": {
                    "expected": matches.coverage.expected,
                    "complete": matches.coverage.complete,
                    "truncated": matches.coverage.truncated,
                    "failed": matches.coverage.failed,
                    "missing": matches.coverage.missing,
                },
                "measured_events": matches.events,
            }
            if (
                matches.events > 0
                and matches.last_match_at is not None
                and (
                    last_fired is None
                    or self._aware(last_fired) < self._aware(matches.last_match_at)
                )
            ):
                last_fired = matches.last_match_at
            if (
                matches.evidence == MatchEvidence.EXACT
                and matches.coverage.state == CoverageState.COMPLETE
            ):
                # A measured count over a fully observed span: exact, and a zero
                # is a real zero.
                trigger_count = matches.events
                measured_exact = True
                metric_inferred = False
                observation_complete = True
                observation_status = "complete"
            else:
                # A lower bound: either the type is not fully searched (COMMON)
                # or some window is missing, TRUNCATED or FAILED. Matches still
                # prove firing; nothing here can prove silence.
                if matches.events > trigger_count:
                    trigger_count = matches.events
                    measured_lower_bound = True
                    metric_inferred = True
                if matches.evidence == MatchEvidence.EXACT:
                    observation_complete = False
                    observation_status = "incomplete"

        def verdict(
            status: RuleHealthStatus,
            reason: str,
            *,
            confidence: float = 1.0,
            building_blocks_healthy: bool | None = None,
            required_log_sources_healthy: bool | None = None,
            missing_dependencies: list[str] | None = None,
            evidence: dict | None = None,
        ) -> HealthVerdict:
            """Build a verdict with the evidence common to every classification.

            Every branch below records the same observation set, so a stored
            snapshot can always re-justify its verdict without re-querying.
            """
            return HealthVerdict(
                status=status,
                reason=reason,
                confidence=confidence,
                window_start=window_start,
                window_end=now,
                last_triggered_at=last_fired,
                trigger_count=trigger_count,
                offense_contribution_count=offense_count,
                expected_daily_firings=expected,
                enabled=rule.enabled,
                building_blocks_healthy=building_blocks_healthy,
                required_log_sources_healthy=required_log_sources_healthy,
                missing_dependencies=missing_dependencies or [],
                evidence={**(evidence or {}), **measurement},
            )

        # 1. Intentionally disabled is a state, not a fault.
        if not rule.enabled:
            return verdict(
                RuleHealthStatus.DISABLED,
                "The rule is disabled in QRadar, so it cannot produce detections.",
            )

        # 2. Too new, or too little observed history, to judge fairly.
        age_days = self._age_days(rule, now)
        if age_days is None:
            return verdict(
                RuleHealthStatus.INSUFFICIENT_DATA,
                "The rule's age is unknown, so inactivity cannot be assessed.",
                confidence=0.3,
            )
        if age_days < self.settings.rule_health_grace_period_days:
            return verdict(
                RuleHealthStatus.INSUFFICIENT_DATA,
                (
                    f"The rule is {age_days:.1f} days old, inside the "
                    f"{self.settings.rule_health_grace_period_days}-day grace period."
                ),
                confidence=0.6,
                evidence={"age_days": round(age_days, 2)},
            )
        if age_days < self.settings.rule_health_min_history_days:
            return verdict(
                RuleHealthStatus.INSUFFICIENT_DATA,
                "Not enough observed history to classify this rule.",
                confidence=0.5,
                evidence={"age_days": round(age_days, 2)},
            )

        # 3. Dependency health. Checked before any silence verdict, because a
        #    rule starved of telemetry tells us nothing about itself.
        missing, bb_ok, ls_ok = self._dependency_state(
            dependencies, building_block_enabled, log_source_type_healthy
        )
        if missing:
            return verdict(
                RuleHealthStatus.DEPENDENCY_DEGRADED,
                (
                    "The rule cannot fire reliably: "
                    + "; ".join(missing[:5])
                    + ". Its silence is not evidence about the rule itself."
                ),
                confidence=0.9,
                building_blocks_healthy=bb_ok,
                required_log_sources_healthy=ls_ok,
                missing_dependencies=missing,
            )

        # 4. Noisy — firing far above the configured expectation.
        window_days = max(1.0, (now - window_start).total_seconds() / 86400.0)
        per_day = trigger_count / window_days
        threshold = (
            expected * 3.0
            if expected is not None and expected > 0
            else self.settings.rule_health_noisy_daily_firings
        )
        if trigger_count > 0 and per_day > threshold:
            return verdict(
                RuleHealthStatus.NOISY,
                (
                    f"The rule fired {per_day:.1f} times a day over the last "
                    f"{window_days:.0f} days, above the expected {threshold:.1f}."
                ),
                building_blocks_healthy=bb_ok,
                required_log_sources_healthy=ls_ok,
                evidence={"firings_per_day": round(per_day, 2), "threshold": threshold},
            )

        # 5. Genuine silence, with dependencies healthy.
        if last_fired is None and trigger_count == 0:
            if not observation_complete:
                # A zero count with no completed collection behind it is an
                # absence of measurement, not an absence of firings. Claiming
                # NEVER_OBSERVED here would report the collector's own gap as a
                # detection gap — the exact confusion this platform exists to
                # remove.
                return verdict(
                    RuleHealthStatus.INSUFFICIENT_DATA,
                    (
                        (
                            "Measured rule-match coverage of the evaluation window is "
                            f"incomplete ({matches.coverage.complete} of "
                            f"{matches.coverage.expected} windows are COMPLETE), so the "
                            "absence of measured matches is not evidence that the rule "
                            "has never fired."
                        )
                        if matches is not None
                        and measurement
                        and matches.evidence == MatchEvidence.EXACT
                        else (
                            "No completed rule-metric collection covers the evaluation "
                            "window, so the absence of observed firings is not evidence "
                            "that the rule has never fired."
                        )
                    ),
                    confidence=0.2,
                    building_blocks_healthy=bb_ok,
                    required_log_sources_healthy=ls_ok,
                    evidence={
                        "age_days": round(age_days, 2),
                        "observation_complete": False,
                        "observation_status": observation_status,
                    },
                )
            return verdict(
                RuleHealthStatus.NEVER_OBSERVED,
                (
                    (
                        "The rule is enabled and its dependencies are healthy, but no "
                        "matching event was measured in any of the "
                        f"{matches.coverage.expected} COMPLETE windows of the evaluation "
                        "span."
                    )
                    if measured_exact and matches is not None
                    else (
                        f"The rule is enabled and its dependencies are healthy, but it "
                        f"has never been observed firing in {age_days:.0f} days."
                    )
                ),
                building_blocks_healthy=bb_ok,
                required_log_sources_healthy=ls_ok,
                evidence={
                    "age_days": round(age_days, 2),
                    "observation_complete": True,
                    "observation_status": observation_status,
                },
            )

        if last_fired is not None:
            quiet_days = (now - self._aware(last_fired)).total_seconds() / 86400.0
            if quiet_days > self.settings.rule_health_inactivity_days:
                return verdict(
                    RuleHealthStatus.INACTIVE,
                    (
                        f"The rule last fired {quiet_days:.0f} days ago, beyond the "
                        f"{self.settings.rule_health_inactivity_days}-day inactivity window."
                    ),
                    building_blocks_healthy=bb_ok,
                    required_log_sources_healthy=ls_ok,
                    evidence={"quiet_days": round(quiet_days, 2)},
                )

        # 6. Working. Note this is reached with offense_count possibly zero: a
        #    rule that fires but creates no offence is still detecting.
        return verdict(
            RuleHealthStatus.HEALTHY,
            (
                (
                    f"At least {trigger_count} matching events were measured in the "
                    "evaluation window with healthy dependencies."
                )
                if measured_lower_bound
                else (
                    f"At least {trigger_count} offense-contributing firings were "
                    "observed in the evaluation window with healthy dependencies."
                )
                if metric_inferred
                else (
                    f"The rule fired {trigger_count} times in the evaluation window "
                    f"with healthy dependencies."
                )
            ),
            building_blocks_healthy=bb_ok,
            required_log_sources_healthy=ls_ok,
            evidence={
                "metric_kind": (
                    "measured_lower_bound"
                    if measured_lower_bound
                    else "inferred" if metric_inferred else "direct"
                ),
                "observation_status": observation_status,
            },
        )

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    def _age_days(self, rule: AnalyticsRule, now: datetime) -> float | None:
        anchor = rule.qradar_created_at or rule.first_seen_at
        if anchor is None:
            return None
        return max(0.0, (now - self._aware(anchor)).total_seconds() / 86400.0)

    def _dependency_state(
        self,
        dependencies: list[RuleDependency],
        building_block_enabled: dict[str, bool],
        log_source_type_healthy: dict[str, bool],
    ) -> tuple[list[str], bool | None, bool | None]:
        """Which dependencies are unmet, and per-kind health summaries.

        Inferred dependencies below the confidence floor are ignored: acting on
        a low-confidence guess would degrade a healthy rule on no real evidence.
        """
        missing: list[str] = []
        bb_seen = ls_seen = False
        bb_ok = ls_ok = True

        for dep in dependencies:
            if dep.confidence < self.settings.coverage_min_confidence:
                continue
            if dep.kind == RuleDependencyKind.BUILDING_BLOCK:
                bb_seen = True
                if building_block_enabled.get(dep.target_ref, True) is False:
                    bb_ok = False
                    label = dep.target_name or f"building block {dep.target_ref}"
                    missing.append(f"{label} is disabled")
            elif dep.kind in (
                RuleDependencyKind.LOG_SOURCE_TYPE,
                RuleDependencyKind.LOG_SOURCE,
            ):
                ls_seen = True
                if log_source_type_healthy.get(dep.target_ref, True) is False:
                    ls_ok = False
                    label = dep.target_name or f"log source type {dep.target_ref}"
                    missing.append(f"{label} is not delivering events")

        return (
            missing,
            bb_ok if bb_seen else None,
            ls_ok if ls_seen else None,
        )

    async def _firing_counts(
        self, rule_ids: list[uuid.UUID], window_start: datetime
    ) -> dict[uuid.UUID, tuple[int, int, bool]]:
        """Counts plus whether any contributing metric is inferred."""
        if not rule_ids:
            return {}
        rows = await self.session.execute(
            select(
                RuleMetric.rule_id,
                func.coalesce(func.sum(RuleMetric.fire_count), 0),
                func.coalesce(func.sum(RuleMetric.offense_contribution_count), 0),
                func.bool_or(RuleMetric.inferred),
            )
            .where(
                RuleMetric.rule_id.in_(rule_ids),
                RuleMetric.bucket_start >= window_start,
            )
            .group_by(RuleMetric.rule_id)
        )
        return {
            rid: (int(fires), int(offenses), bool(inferred))
            for rid, fires, offenses, inferred in rows.all()
        }

    async def _match_coverage(
        self, instance_id: uuid.UUID, window_start: datetime, now: datetime
    ) -> MatchCoverage:
        """How much of the evaluation span the match windows cover.

        Two queries for the whole instance, whatever the number of rules.
        """
        ever = (
            await self.session.scalar(
                select(RuleMatchWindow.instance_id)
                .where(RuleMatchWindow.instance_id == instance_id)
                .limit(1)
            )
        ) is not None
        rows: list[tuple[datetime, int, str]] = []
        if ever:
            result = await self.session.execute(
                select(
                    RuleMatchWindow.window_start,
                    RuleMatchWindow.window_seconds,
                    RuleMatchWindow.status,
                ).where(
                    RuleMatchWindow.instance_id == instance_id,
                    RuleMatchWindow.window_start >= window_start,
                )
            )
            rows = [(start, seconds, str(status)) for start, seconds, status in result.all()]
        return assess_coverage(
            rows,
            evaluation_start=window_start,
            now=now,
            window_seconds=self.settings.rule_match_window_seconds,
            settle_seconds=self.settings.rule_match_settle_lag_seconds,
            ever_collected=ever,
        )

    async def _match_sums(
        self, rule_ids: list[uuid.UUID], window_start: datetime
    ) -> dict[uuid.UUID, tuple[int, datetime | None]]:
        """Measured events and latest matching window start, per rule."""
        if not rule_ids:
            return {}
        # Only windows on the evaluation grid count, as the coverage check does:
        # a bucket straddling the span's start is not part of what was verified.
        first = floor_to_window(window_start, self.settings.rule_match_window_seconds)
        if first < window_start:
            first += timedelta(seconds=self.settings.rule_match_window_seconds)
        rows = await self.session.execute(
            select(
                RuleMatchMetric.rule_id,
                func.coalesce(func.sum(RuleMatchMetric.matched_events), 0),
                func.max(RuleMatchMetric.bucket_start),
            )
            .where(
                RuleMatchMetric.rule_id.in_(rule_ids),
                RuleMatchMetric.bucket_start >= first,
            )
            .group_by(RuleMatchMetric.rule_id)
        )
        return {rid: (int(events), last) for rid, events, last in rows.all()}

    async def _observation_status(
        self, instance_id: uuid.UUID, window_start: datetime
    ) -> str:
        """Return ``never_run``, ``incomplete`` or ``complete``.

        Offense-contribution collection deliberately records ``incomplete``:
        positive rows prove a lower bound, but no row can prove a zero.  Older
        direct collectors that predate metadata are treated as complete to
        preserve the meaning of their existing watermarks.
        """
        watermark = await self.session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance_id,
                CollectionWatermark.collector == RULE_METRIC_COLLECTOR,
            )
        )
        if watermark is None or watermark.intervals_collected <= 0:
            return "never_run"
        if watermark.watermark_at is None:
            return "never_run"
        metadata = watermark.collection_metadata or {}
        if metadata.get("completeness", "complete") != "complete":
            return "incomplete"
        if self._aware(watermark.watermark_at) < window_start:
            return "incomplete"
        return "complete"

    async def _observation_complete(
        self, instance_id: uuid.UUID, window_start: datetime
    ) -> bool:
        """Compatibility helper for callers/tests that only need the gate."""
        return await self._observation_status(instance_id, window_start) == "complete"

    async def _dependencies(
        self, rule_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[RuleDependency]]:
        if not rule_ids:
            return {}
        rows = (
            await self.session.scalars(
                select(RuleDependency).where(RuleDependency.rule_id.in_(rule_ids))
            )
        ).all()
        out: dict[uuid.UUID, list[RuleDependency]] = {}
        for dep in rows:
            out.setdefault(dep.rule_id, []).append(dep)
        return out

    async def _building_block_state(self, instance_id: uuid.UUID) -> dict[str, bool]:
        rows = await self.session.execute(
            select(AnalyticsRule.qradar_id, AnalyticsRule.enabled).where(
                AnalyticsRule.instance_id == instance_id,
                AnalyticsRule.is_building_block.is_(True),
            )
        )
        return {str(qid): bool(enabled) for qid, enabled in rows.all()}

    async def _log_source_type_health(
        self, instance_id: uuid.UUID, now: datetime
    ) -> dict[str, bool]:
        """Is at least one enabled source of each type delivering events?

        A type counts as healthy when any monitored source of that type reported
        an event recently. Sources in maintenance are excluded from the verdict
        entirely rather than counted as unhealthy — planned downtime must not
        present as a detection gap.
        """
        rows = (
            await self.session.scalars(
                select(LogSource).where(LogSource.instance_id == instance_id)
            )
        ).all()

        cutoff = now - timedelta(hours=24)
        by_type: dict[str, bool] = {}
        for source in rows:
            if source.type_id is None:
                continue
            key = str(source.type_id)
            in_maintenance = source.maintenance_mode and (
                source.maintenance_until is None
                or self._aware(source.maintenance_until) > now
            )
            if in_maintenance or not source.monitoring_enabled:
                # Neither evidence for nor against; leave any existing verdict.
                by_type.setdefault(key, True)
                continue
            healthy = bool(
                source.enabled
                and source.last_event_time is not None
                and self._aware(source.last_event_time) >= cutoff
            )
            by_type[key] = by_type.get(key, False) or healthy
        return by_type

    # ----------------------------------------------------------- persistence
    async def _persist(
        self, rule: AnalyticsRule, verdict: HealthVerdict, *, now: datetime
    ) -> bool:
        values = {
            "rule_id": rule.id,
            "evaluated_at": now,
            "status": verdict.status,
            "logic_version": self.logic_version,
            "confidence": verdict.confidence,
            "reason": verdict.reason,
            "window_start": verdict.window_start,
            "window_end": verdict.window_end,
            "last_triggered_at": verdict.last_triggered_at,
            "trigger_count": verdict.trigger_count,
            "offense_contribution_count": verdict.offense_contribution_count,
            "expected_daily_firings": verdict.expected_daily_firings,
            "enabled": verdict.enabled,
            "building_blocks_healthy": verdict.building_blocks_healthy,
            "required_log_sources_healthy": verdict.required_log_sources_healthy,
            "missing_dependencies": verdict.missing_dependencies,
            "evidence": verdict.evidence,
        }
        stmt = pg_insert(RuleHealthSnapshot).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["rule_id", "evaluated_at"],
            set_={k: v for k, v in values.items() if k not in ("rule_id", "evaluated_at")},
        )
        await self.session.execute(stmt)
        return True

    async def _apply_with_flap_damping(
        self, rule: AnalyticsRule, verdict: HealthVerdict, *, now: datetime
    ) -> bool:
        """Update the rule's stored verdict only once it has settled.

        A rule that fires every few weeks would otherwise oscillate between
        HEALTHY and INACTIVE on each evaluation, and anything alerting on the
        stored status would flap with it.
        """
        rule.health_evaluated_at = now
        if rule.health_status == verdict.status:
            return False

        threshold = self.settings.rule_health_flap_threshold
        if threshold <= 1:
            rule.health_status = verdict.status
            return True

        recent = list(
            (
                await self.session.scalars(
                    select(RuleHealthSnapshot.status)
                    .where(RuleHealthSnapshot.rule_id == rule.id)
                    .order_by(RuleHealthSnapshot.evaluated_at.desc())
                    .limit(threshold)
                )
            ).all()
        )
        if len(recent) >= threshold and all(s == verdict.status for s in recent):
            rule.health_status = verdict.status
            return True
        return False
