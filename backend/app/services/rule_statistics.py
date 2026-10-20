"""Rule statistics: counts, measured matches, silence and the capacity benchmark.

Read-only and constant-cost: a fixed number of grouped queries however many
rules the instance holds (one per section, never one per rule).

Every measured number rests on `rule_match_window`. A rule's absence from
`rule_match_metric` means something only where the window proves it
(`match_evidence`), so:

  * the span is judged by the same `assess_coverage` rule health uses: one
    missing, TRUNCATED or FAILED window and no rule's silence is proven;
  * match sums and rates count only COMPLETE windows, and the rate's divisor is
    the seconds those windows cover, so a half-collected span never reads as a
    low rate;
  * a section that cannot be measured is None with a reason, never zero.

`average_capacity` is QRadar's benchmark of how fast a rule could be evaluated.
It is reported as that, and never as a firing rate (design §E.1).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.history.origin import HistoryOrigin
from app.models.rule import (
    AnalyticsRule,
    RuleMatchMetric,
    RuleMatchWindow,
    RuleMatchWindowStatus,
)
from app.rules.matches import (
    CoverageState,
    MatchCoverage,
    MatchEvidence,
    assess_coverage,
    floor_to_window,
    match_evidence,
)
from app.schemas.rule_statistics import (
    CostRow,
    CostSection,
    MatchRankRow,
    RuleCountRow,
    RuleStatistics,
    SilentExclusion,
    SilentRule,
    SilentSection,
    StatisticsWindow,
)

#: The silent list is capped; `total` still counts every match.
SILENT_ITEM_CAP = 500

BENCHMARK_LABEL = (
    "QRadar capacity benchmark: how fast the rule could be evaluated. "
    "Lower is costlier to evaluate. This is not a firing rate."
)
BENCHMARK_UNIT = "millions of events per second (QRadar's unit)"

SILENT_EXCLUDED_REASON = (
    "Only EVENT rules and EVENT building blocks are judged: an event search sees every one of "
    "their matches. COMMON rules also match on flows, which are not searched, so their silence "
    "is unknown. FLOW and OFFENSE rules are not searched at all."
)


def _window_reason(coverage: MatchCoverage) -> str | None:
    if coverage.state is CoverageState.COMPLETE:
        return None
    if coverage.state is CoverageState.NONE:
        return (
            "No rule-match window has been collected for this instance: collection is disabled "
            "or has not run yet, so nothing is measured."
        )
    if coverage.expected == 0:
        return "No rule-match window in the requested span has settled yet."
    return (
        f"Only {coverage.complete} of {coverage.expected} windows in the span are complete "
        f"({coverage.missing} missing, {coverage.truncated} truncated, {coverage.failed} failed)."
        " Absence from an incomplete window proves nothing."
    )


class RuleStatisticsService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def compute(
        self, instance_id: uuid.UUID, *, days: int, top: int, now: datetime | None = None
    ) -> RuleStatistics:
        now = now or datetime.now(UTC)
        ws = self.settings.rule_match_window_seconds
        settled_until = floor_to_window(
            now - timedelta(seconds=self.settings.rule_match_settle_lag_seconds), ws
        )
        span_start = floor_to_window(settled_until - timedelta(days=days), ws)

        counts = await self._counts(instance_id)
        coverage, history_windows = await self._coverage(instance_id, span_start, now)
        window = StatisticsWindow(
            days=days,
            span_start=span_start,
            span_end=settled_until,
            span_seconds=int((settled_until - span_start).total_seconds()),
            window_seconds=ws,
            expected_windows=coverage.expected,
            complete_windows=coverage.complete,
            history_windows=history_windows,
            truncated_windows=coverage.truncated,
            failed_windows=coverage.failed,
            missing_windows=coverage.missing,
            measured_seconds=coverage.complete * ws,
            complete=coverage.state is CoverageState.COMPLETE,
            reason=_window_reason(coverage),
        )

        silent: SilentSection | None = None
        silent_reason = window.reason
        if window.complete:
            silent = await self._silent(instance_id, span_start, settled_until, counts)
            silent_reason = None

        top_by_matches = top_by_events = None
        top_reason = window.reason
        if coverage.complete > 0:
            top_by_matches = await self._top(
                instance_id, span_start, settled_until, top, window.measured_seconds,
                by_events=False,
            )
            top_by_events = await self._top(
                instance_id, span_start, settled_until, top, window.measured_seconds,
                by_events=True,
            )
        elif top_reason is None:  # pragma: no cover - complete implies a window
            top_reason = "No complete window to measure."

        return RuleStatistics(
            instance_id=instance_id,
            counts=counts,
            window=window,
            silent=silent,
            silent_reason=silent_reason,
            top_by_matches=top_by_matches,
            top_by_events=top_by_events,
            top_reason=top_reason,
            costliest_by_benchmark=await self._costliest(instance_id, top),
        )

    # ---------------------------------------------------------------- counts
    async def _counts(self, instance_id: uuid.UUID) -> list[RuleCountRow]:
        rows = await self.session.execute(
            select(
                AnalyticsRule.enabled,
                AnalyticsRule.rule_type,
                AnalyticsRule.origin,
                AnalyticsRule.is_building_block,
                func.count(),
            )
            .where(AnalyticsRule.instance_id == instance_id)
            .group_by(
                AnalyticsRule.enabled,
                AnalyticsRule.rule_type,
                AnalyticsRule.origin,
                AnalyticsRule.is_building_block,
            )
        )
        out = [
            RuleCountRow(
                enabled=enabled,
                rule_type=rule_type,
                origin=origin,
                is_building_block=is_bb,
                count=int(count),
            )
            for enabled, rule_type, origin, is_bb, count in rows.all()
        ]
        out.sort(
            key=lambda r: (
                not r.enabled,
                r.rule_type or "",
                r.origin or "",
                r.is_building_block,
            )
        )
        return out

    # -------------------------------------------------------------- coverage
    async def _coverage(
        self, instance_id: uuid.UUID, span_start: datetime, now: datetime
    ) -> tuple[MatchCoverage, int]:
        """The span's coverage, and how many of its COMPLETE windows came from history.

        The marker rides the same select as the window rows (§B.4.5), so the
        count costs no statement. `assess_coverage` stays pure and untouched:
        the history windows are assessed by the same function, over the history
        rows alone, which counts exactly the grid windows the backfill read.
        """
        ever = (
            await self.session.scalar(
                select(RuleMatchWindow.instance_id)
                .where(RuleMatchWindow.instance_id == instance_id)
                .limit(1)
            )
        ) is not None
        rows: list[tuple[datetime, int, str, str | None]] = []
        if ever:
            result = await self.session.execute(
                select(
                    RuleMatchWindow.window_start,
                    RuleMatchWindow.window_seconds,
                    RuleMatchWindow.status,
                    RuleMatchWindow.origin,
                ).where(
                    RuleMatchWindow.instance_id == instance_id,
                    RuleMatchWindow.window_start >= span_start,
                )
            )
            rows = [
                (start, secs, str(status), origin)
                for start, secs, status, origin in result.all()
            ]
        coverage = assess_coverage(
            [(start, secs, status) for start, secs, status, _origin in rows],
            evaluation_start=span_start,
            now=now,
            window_seconds=self.settings.rule_match_window_seconds,
            settle_seconds=self.settings.rule_match_settle_lag_seconds,
            ever_collected=ever,
        )
        history = assess_coverage(
            [
                (start, secs, status)
                for start, secs, status, origin in rows
                if origin == HistoryOrigin.HISTORY.value
            ],
            evaluation_start=span_start,
            now=now,
            window_seconds=self.settings.rule_match_window_seconds,
            settle_seconds=self.settings.rule_match_settle_lag_seconds,
            ever_collected=ever,
        )
        return coverage, history.complete

    # ---------------------------------------------------------------- silent
    async def _silent(
        self,
        instance_id: uuid.UUID,
        span_start: datetime,
        span_end: datetime,
        counts: list[RuleCountRow],
    ) -> SilentSection:
        matched = (
            select(RuleMatchMetric.rule_id)
            .where(
                RuleMatchMetric.rule_id == AnalyticsRule.id,
                RuleMatchMetric.bucket_start >= span_start,
                RuleMatchMetric.bucket_start < span_end,
            )
            .exists()
        )
        rows = (
            await self.session.execute(
                select(AnalyticsRule, func.count().over())
                .where(
                    AnalyticsRule.instance_id == instance_id,
                    AnalyticsRule.enabled.is_(True),
                    # Mirrors match_evidence(...) == EXACT.
                    func.upper(func.trim(AnalyticsRule.rule_type)) == "EVENT",
                    ~matched,
                )
                .order_by(AnalyticsRule.name, AnalyticsRule.qradar_id)
                .limit(SILENT_ITEM_CAP)
            )
        ).all()
        total = int(rows[0][1]) if rows else 0

        excluded: dict[str | None, int] = defaultdict(int)
        evidence: dict[str | None, MatchEvidence] = {}
        for row in counts:
            if row.enabled and match_evidence(row.rule_type) is not MatchEvidence.EXACT:
                excluded[row.rule_type] += row.count
                evidence[row.rule_type] = match_evidence(row.rule_type)
        return SilentSection(
            items=[
                SilentRule(
                    rule_id=rule.id,
                    qradar_id=rule.qradar_id,
                    name=rule.name,
                    rule_type=rule.rule_type,
                    origin=rule.origin,
                    is_building_block=rule.is_building_block,
                    first_seen_at=rule.first_seen_at,
                    last_fired_at=rule.last_fired_at,
                )
                for rule, _ in rows
            ],
            total=total,
            items_truncated=total > len(rows),
            excluded=[
                SilentExclusion(rule_type=kind, evidence=evidence[kind].value, count=n)
                for kind, n in sorted(excluded.items(), key=lambda kv: kv[0] or "")
            ],
            excluded_count=sum(excluded.values()),
            excluded_reason=SILENT_EXCLUDED_REASON,
        )

    # ------------------------------------------------------------------ tops
    async def _top(
        self,
        instance_id: uuid.UUID,
        span_start: datetime,
        span_end: datetime,
        top: int,
        measured_seconds: int,
        *,
        by_events: bool,
    ) -> list[MatchRankRow]:
        records = func.sum(RuleMatchMetric.match_records)
        events = func.sum(RuleMatchMetric.matched_events)
        ordering = events if by_events else records
        stmt: Select = (
            select(AnalyticsRule, records, events)
            .join(AnalyticsRule, AnalyticsRule.id == RuleMatchMetric.rule_id)
            .join(
                RuleMatchWindow,
                (RuleMatchWindow.instance_id == AnalyticsRule.instance_id)
                & (RuleMatchWindow.window_start == RuleMatchMetric.bucket_start),
            )
            .where(
                AnalyticsRule.instance_id == instance_id,
                RuleMatchWindow.status == RuleMatchWindowStatus.COMPLETE,
                RuleMatchWindow.window_seconds == self.settings.rule_match_window_seconds,
                RuleMatchMetric.bucket_start >= span_start,
                RuleMatchMetric.bucket_start < span_end,
            )
            .group_by(AnalyticsRule.id)
            .order_by(ordering.desc(), AnalyticsRule.name, AnalyticsRule.qradar_id)
            .limit(top)
        )
        out: list[MatchRankRow] = []
        for rule, rec, ev in (await self.session.execute(stmt)).all():
            out.append(
                MatchRankRow(
                    rule_id=rule.id,
                    qradar_id=rule.qradar_id,
                    name=rule.name,
                    enabled=rule.enabled,
                    rule_type=rule.rule_type,
                    is_building_block=rule.is_building_block,
                    match_records=int(rec),
                    matched_events=int(ev),
                    match_rate_per_second=int(ev) / measured_seconds,
                    lower_bound=match_evidence(rule.rule_type) is not MatchEvidence.EXACT,
                )
            )
        return out

    # ------------------------------------------------------------- benchmark
    async def _costliest(self, instance_id: uuid.UUID, top: int) -> CostSection:
        has_benchmark = AnalyticsRule.average_capacity > 0
        rows = await self.session.scalars(
            select(AnalyticsRule)
            .where(
                AnalyticsRule.instance_id == instance_id,
                AnalyticsRule.enabled.is_(True),
                has_benchmark,
            )
            .order_by(AnalyticsRule.average_capacity, AnalyticsRule.name)
            .limit(top)
        )
        missing = await self.session.scalar(
            select(func.count()).where(
                AnalyticsRule.instance_id == instance_id,
                AnalyticsRule.enabled.is_(True),
                ~func.coalesce(has_benchmark, False),
            )
        )
        return CostSection(
            label=BENCHMARK_LABEL,
            unit=BENCHMARK_UNIT,
            items=[
                CostRow(
                    rule_id=r.id,
                    qradar_id=r.qradar_id,
                    name=r.name,
                    rule_type=r.rule_type,
                    is_building_block=r.is_building_block,
                    average_capacity=float(r.average_capacity or 0.0),
                )
                for r in rows.all()
            ],
            no_benchmark_count=int(missing or 0),
        )
