"""Rule silence and storm over one collected run (SOC-6 design §5.4, §6).

Runs at the end of ``collect_rule_matches``, inside the instance session, after
``RuleMatchCollector.collect``. One run loads one series and judges every settled
window of that run against it, oldest first. The storm cell is one grouped
summary, not a pull of every historical sample. Writes are one
``INSERT ... FROM unnest`` per table, so a run of 100 rules and a run of 5,000
issue the same statements.

The family switch is the outer gate. Incidents are recorded either way. Alerts
are opened only when ``rule_alerting_enabled`` and ``rule_match_collection_enabled``
are both true, and then only after the alert policy says ``ALERT``. A suppression
is consulted only on that ``ALERT`` decision. ``INSUFFICIENT_DATA`` never resolves
an open incident. A disabled rule, or a rule whose explicit building-block
dependency is disabled, resolves its open incidents in the same bulk write.

The collector releases its advisory lock before this runs (§8 forbids editing the
collector). Two overlapping ticks can race on detector state; they still judge
different ``collected_at`` windows. A failure here propagates, so the collection
transaction rolls back with the judgement.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    bindparam,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from app.alerts.dispatcher import AlertEnqueuer
from app.alerts.fingerprint import rule_anomaly_fingerprint
from app.alerts.policy import AlertPolicyGate
from app.alerts.service import AlertInput, AlertResolution, AlertService
from app.alerts.suppression import SuppressionGate, record_hit
from app.anomaly.evidence import AnomalyEvidence
from app.anomaly.lifecycle import LifecycleInput, advance_counters, next_state
from app.anomaly.seasonal import CellSummary
from app.collectors.rule_collector import COLLECTOR_NAME as RULE_INVENTORY
from app.core.config import Settings, get_settings
from app.models.enums import (
    ACTIVE_ANOMALY_STATES,
    AlertPolicyMode,
    AnomalyFamily,
    AnomalyState,
    EvidenceStatus,
    RuleAnomalyType,
    Severity,
    WafMeasurement,
)
from app.models.instance import QRadarInstance
from app.models.rule import AnalyticsRule
from app.models.rule_anomaly import RuleAnomaly, RuleDetectorState
from app.rules.anomalies import (
    DETECTOR_LABELS,
    RuleCell,
    RuleSubject,
    RuleThresholds,
    SilenceContext,
    StormContext,
    WindowFact,
    detect_silence,
    detect_storm,
    slot_of,
)

ACTOR = "rule-engine"
_SOURCE_TYPE = "analytics_rule"
_DETECTORS = (RuleAnomalyType.RULE_SILENCE.value, RuleAnomalyType.RULE_STORM.value)
_DAY_SECONDS = 86_400
_TITLES = DETECTOR_LABELS
# Typed binds so an all-NULL array (a fresh state's anomaly ids, a silence
# row's unused z-score) still has a Postgres type. asyncpg cannot infer one
# from a list of None, and a later CAST cannot recover a parameter it rejected.
_UUIDS = ARRAY(PgUUID(as_uuid=True))
_TIMESTAMPS = ARRAY(DateTime(timezone=True))
_FLOATS = ARRAY(Float)
_TEXTS = ARRAY(String)
_INTS = ARRAY(Integer)
_BOOLS = ARRAY(Boolean)


def _statement(sql: str, **types: Any) -> TextClause:
    """A text statement whose array parameters carry an explicit Postgres type."""
    clause = text(sql)
    if not types:
        return clause
    return clause.bindparams(
        *(bindparam(name, type_=kind) for name, kind in types.items())
    )


@dataclass
class RuleEvaluationReport:
    """What one instance run judged. Statement shape is not on this object."""

    windows_judged: int = 0
    rules_seen: int = 0
    incidents_opened: int = 0
    incidents_resolved: int = 0
    skipped: str | None = None


@dataclass(frozen=True)
class _WindowRow:
    start: datetime
    window_seconds: int
    status: str
    origin: str
    unknown_ids: int


@dataclass(frozen=True)
class _RuleRow:
    id: uuid.UUID
    name: str
    subject: RuleSubject


@dataclass
class _State:
    id: uuid.UUID
    rule_id: uuid.UUID
    detector: str
    consecutive_anomalous: int
    consecutive_healthy: int
    state: AnomalyState
    last_interval_start: datetime | None
    active_anomaly_id: uuid.UUID | None
    open_anomaly_id: uuid.UUID | None
    persisted: bool = True
    dirty: bool = False


@dataclass
class _Incident:
    id: uuid.UUID
    rule_id: uuid.UUID
    qradar_id: int
    is_building_block: bool
    detector: str
    fingerprint: str
    severity: str
    state: str
    detected_at: datetime
    opened_at: datetime | None
    anomaly_start: datetime | None
    anomaly_end: datetime | None
    resolved_at: datetime | None
    observed_value: float | None
    expected_value: float | None
    deviation_score: float | None
    deviation_ratio: float | None
    robust_z: float | None
    absolute_delta: float | None
    confidence: float | None
    consecutive_buckets: int
    policy_version: int
    explanation: str | None
    details: dict[str, Any]
    dirty: bool = False


@dataclass
class _Transition:
    id: uuid.UUID
    anomaly_id: uuid.UUID
    from_state: str | None
    to_state: str
    occurred_at: datetime
    bucket_start: datetime | None
    reason: str | None
    reason_i18n: dict[str, Any] | None
    observed: float | None
    expected: float | None


@dataclass
class _AlertOpen:
    rule_id: uuid.UUID
    rule_name: str
    qradar_id: int
    is_building_block: bool
    detector: str
    evidence: AnomalyEvidence[RuleAnomalyType]
    window_start: datetime
    incident_id: uuid.UUID


@dataclass(frozen=True)
class _AlertResolve:
    rule_id: uuid.UUID
    detector: str
    reason: str


@dataclass
class _Series:
    facts: list[WindowFact]
    by_start: dict[datetime, WindowFact]


@dataclass
class _Batch:
    states: dict[tuple[uuid.UUID, str], _State] = field(default_factory=dict)
    incidents: dict[uuid.UUID, _Incident] = field(default_factory=dict)
    open_by: dict[tuple[uuid.UUID, str], _Incident] = field(default_factory=dict)
    transitions: list[_Transition] = field(default_factory=list)
    alerts: list[_AlertOpen | _AlertResolve] = field(default_factory=list)


@dataclass
class RuleSectionRead:
    """One rule, judged the way the engine would judge its newest window.

    Built by ``RuleMatchEngine.read_rule``. The batch path does not use it.
    ``judged_through`` is the end of the newest settled window, or None when
    nothing has settled. ``silence`` and ``storm`` are None when no window
    has been recorded.
    """

    rule_id: uuid.UUID
    subject: RuleSubject
    thresholds: RuleThresholds
    watermark: datetime | None
    any_window: bool
    judged_start: datetime | None
    judged_through: datetime | None
    silence: AnomalyEvidence[RuleAnomalyType] | None
    storm: AnomalyEvidence[RuleAnomalyType] | None
    incidents: tuple[RuleAnomaly, ...]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _optional_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value)
    raise TypeError(f"expected a timestamp, got {type(value).__name__}")


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _ints(value: object) -> tuple[int, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(int(item) for item in value)


def _jsonable(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, default=str)
    loaded = json.loads(encoded)
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _measurements(evidence: AnomalyEvidence[RuleAnomalyType]) -> dict[str, float | None]:
    extra = evidence.extra or {}
    observed = evidence.observed_value
    expected = evidence.expected_value
    ratio = extra.get("deviation_ratio")
    if ratio is None:
        ratio = extra.get("ratio")
    if ratio is None:
        ratio = extra.get("ratio_to_baseline")
    delta = extra.get("absolute_delta")
    if delta is None and observed is not None and expected is not None:
        delta = observed - expected
    return {
        "observed_value": observed,
        "expected_value": expected,
        "deviation_score": evidence.deviation_score,
        "deviation_ratio": _as_float(ratio),
        "robust_z": _as_float(extra.get("robust_z")),
        "absolute_delta": _as_float(delta),
        "confidence": evidence.confidence,
    }


def _disabled_reason(watermark: datetime | None) -> str:
    if watermark is None:
        return "disabled in the inventory sync"
    stamp = _aware(watermark)
    return f"disabled in the inventory sync at {stamp:%Y-%m-%d %H:%M} UTC"


def _dependency_reason(subject: RuleSubject) -> str:
    ids = ", ".join(str(item) for item in subject.disabled_dependencies)
    return (
        "Not judged: a stored building-block dependency of this rule is disabled "
        f"(QRadar ids {ids})."
    )


def _title(detector: str, name: str, qradar_id: int) -> str:
    text_value = f"{_TITLES.get(detector, detector)}: {name} (QRadar id {qradar_id})"
    if len(text_value) <= 512:
        return text_value
    return text_value[:509] + "..."


def _fresh_state(rule_id: uuid.UUID, detector: str) -> _State:
    return _State(
        id=uuid.uuid4(),
        rule_id=rule_id,
        detector=detector,
        consecutive_anomalous=0,
        consecutive_healthy=0,
        state=AnomalyState.NORMAL,
        last_interval_start=None,
        active_anomaly_id=None,
        open_anomaly_id=None,
        persisted=False,
    )


class RuleMatchEngine:
    """Judge the windows one collection run wrote, for one instance."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Any = None,
        enqueuer: AlertEnqueuer | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._enqueuer = enqueuer
        self.alerts = AlertService(session, clock=self._clock)
        self._policy = AlertPolicyGate(
            session, default=AlertPolicyMode(self.settings.alert_policy_default)
        )
        self._suppression = SuppressionGate(session)

    @property
    def _alerting(self) -> bool:
        return bool(
            self.settings.rule_alerting_enabled and self.settings.rule_match_collection_enabled
        )

    async def evaluate(
        self,
        instance: QRadarInstance,
        *,
        window_starts: Sequence[datetime] | None = None,
        written_since: datetime | None = None,
    ) -> RuleEvaluationReport:
        """Judge ``window_starts``, or the windows this run collected.

        ``window_starts`` skips the collected-at probe so a test can judge
        historical windows without their ``collected_at`` pulling extra rows.
        An empty start list, or a probe that finds nothing, issues no further
        statement. Collection disabled is the same no-op.
        """
        report = RuleEvaluationReport()
        if not self.settings.rule_match_collection_enabled:
            report.skipped = "collection_disabled"
            return report
        if window_starts is None:
            if written_since is None:
                report.skipped = "no_window"
                return report
            found = await self._probe(instance.id, written_since)
            if not found:
                report.skipped = "no_window"
                return report
            window_starts = found
        starts = sorted({_aware(item) for item in window_starts})
        if not starts:
            report.skipped = "no_window"
            return report

        await self.session.flush()
        width = self.settings.rule_match_window_seconds
        thresholds = self._thresholds()
        margin = timedelta(seconds=width * self.settings.rule_silence_windows)
        lookback_start = starts[0] - timedelta(days=self.settings.rule_storm_lookback_days)
        span_start = starts[0] - timedelta(days=thresholds.silence_baseline_days) - margin
        load_from = min(lookback_start, span_start)
        newest = starts[-1]
        newest_end = newest + timedelta(seconds=width)
        instance_id = instance.id
        instance_name = instance.name

        windows = await self._load_windows(instance_id, load_from, newest)
        rules = await self._load_rules(instance_id)
        transitions = await self._load_transitions(instance_id, load_from, newest_end)
        watermark = await self._load_watermark(instance_id)
        metrics = await self._load_metrics(instance_id, load_from, newest)
        cells = await self._load_cells(
            instance_id, lookback_start, newest, newest_end, width, starts, thresholds
        )
        batch = _Batch()
        await self._load_states(instance_id, batch)
        await self._load_open_incidents(instance_id, batch)
        for rule in rules:
            rule = _with_transitions(rule, transitions.get(rule.id, ()))
            if not rule.subject.enabled:
                self._resolve_subject(
                    rule, _disabled_reason(watermark), batch, newest, report
                )
                continue
            if rule.subject.disabled_dependencies:
                self._resolve_subject(
                    rule, _dependency_reason(rule.subject), batch, newest, report
                )
                continue
            series = _series_for(rule.id, windows, metrics)
            for detector in _DETECTORS:
                self._judge_detector(
                    instance_id,
                    instance_name,
                    rule,
                    detector,
                    starts,
                    series,
                    cells,
                    watermark,
                    thresholds,
                    width,
                    batch,
                    report,
                )
        report.windows_judged = len(starts)
        report.rules_seen = len(rules)
        await self._write(batch)
        self.session.expire_all()
        if batch.alerts:
            await self._apply_alerts(instance_id, instance_name, batch.alerts, width)
        return report

    def _thresholds(self) -> RuleThresholds:
        settings = self.settings
        return RuleThresholds(
            silence_windows=settings.rule_silence_windows,
            silence_min_presence=settings.rule_silence_min_presence,
            silence_min_median=settings.rule_silence_min_median,
            storm_min_samples=settings.rule_storm_min_samples,
            storm_min_visits=settings.rule_storm_min_visits,
            storm_min_window_events=settings.rule_storm_min_window_events,
            storm_ratio=settings.rule_storm_ratio,
            storm_min_absolute_delta=settings.rule_storm_min_absolute_delta,
            deviation_z=settings.anomaly_deviation_threshold,
            mad_floor_ratio=settings.baseline_mad_floor_ratio,
        )

    def _open_after(self, detector: str) -> int:
        if detector == RuleAnomalyType.RULE_SILENCE.value:
            return self.settings.rule_silence_windows
        return self.settings.anomaly_open_after_intervals

    async def _probe(self, instance_id: uuid.UUID, written_since: datetime) -> list[datetime]:
        rows = await self.session.execute(
            text(
                """
                -- rule-engine probe
                SELECT window_start
                FROM rule_match_window
                WHERE instance_id = :instance_id
                  AND collected_at >= :written_since
                ORDER BY window_start
                """
            ),
            {"instance_id": instance_id, "written_since": written_since},
        )
        return [_aware(row[0]) for row in rows if row[0] is not None]

    async def _load_windows(
        self, instance_id: uuid.UUID, load_from: datetime, newest: datetime
    ) -> list[_WindowRow]:
        rows = await self.session.execute(
            text(
                """
                -- rule-engine windows
                SELECT window_start, window_seconds, status, origin, unknown_ids
                FROM rule_match_window
                WHERE instance_id = :instance_id
                  AND window_start >= :load_from
                  AND window_start <= :newest
                ORDER BY window_start
                """
            ),
            {"instance_id": instance_id, "load_from": load_from, "newest": newest},
        )
        return [
            _WindowRow(
                start=_aware(row[0]),
                window_seconds=int(row[1]),
                status=str(row[2]),
                origin=str(row[3]),
                unknown_ids=int(row[4]),
            )
            for row in rows
        ]

    async def _load_rules(self, instance_id: uuid.UUID) -> list[_RuleRow]:
        rows = (
            await self.session.execute(
                text(
                    """
                    -- rule-engine rules
                    SELECT
                        r.id, r.name, r.qradar_id, r.rule_type, r.enabled,
                        r.is_building_block, r.health_monitoring_enabled,
                        r.first_seen_at, r.qradar_created_at, r.qradar_modified_at,
                        COALESCE(dep.disabled_ids, ARRAY[]::integer[]) AS disabled_ids,
                        COALESCE(dep.unobserved_ids, ARRAY[]::integer[]) AS unobserved_ids,
                        COALESCE(dep.dependency_count, 0) AS dependency_count
                    FROM analytics_rule r
                    LEFT JOIN LATERAL (
                        SELECT
                            array_agg(x.qid) FILTER (WHERE x.disabled) AS disabled_ids,
                            array_agg(x.qid) FILTER (WHERE x.unobserved) AS unobserved_ids,
                            count(*) AS dependency_count
                        FROM (
                            SELECT
                                CASE
                                    WHEN d.target_ref ~ '^[0-9]+$'
                                    THEN CAST(d.target_ref AS integer)
                                    ELSE -1
                                END AS qid,
                                (tgt.id IS NOT NULL AND tgt.enabled IS FALSE) AS disabled,
                                (tgt.id IS NULL) AS unobserved
                            FROM rule_dependency d
                            LEFT JOIN analytics_rule tgt
                              ON tgt.instance_id = r.instance_id
                             AND tgt.qradar_id = CASE
                                    WHEN d.target_ref ~ '^[0-9]+$'
                                    THEN CAST(d.target_ref AS integer)
                                    ELSE NULL
                                 END
                            WHERE d.rule_id = r.id
                              AND d.source = 'EXPLICIT'
                              AND d.kind = 'BUILDING_BLOCK'
                        ) x
                    ) dep ON true
                    WHERE r.instance_id = :instance_id
                    """
                ),
                {"instance_id": instance_id},
            )
        ).mappings()
        out: list[_RuleRow] = []
        for row in rows:
            out.append(
                _RuleRow(
                    id=_uuid(row["id"]),
                    name=str(row["name"]),
                    subject=RuleSubject(
                        qradar_id=int(row["qradar_id"]),
                        rule_type=row["rule_type"] if row["rule_type"] is not None else None,
                        enabled=bool(row["enabled"]),
                        is_building_block=bool(row["is_building_block"]),
                        health_monitoring_enabled=bool(row["health_monitoring_enabled"]),
                        first_seen_at=_optional_dt(row["first_seen_at"]),
                        qradar_created_at=_optional_dt(row["qradar_created_at"]),
                        qradar_modified_at=_optional_dt(row["qradar_modified_at"]),
                        disabled_dependencies=_ints(row["disabled_ids"]),
                        unobserved_dependencies=_ints(row["unobserved_ids"]),
                        dependency_count=int(row["dependency_count"] or 0),
                    ),
                )
            )
        return out

    async def _load_transitions(
        self, instance_id: uuid.UUID, load_from: datetime, newest_end: datetime
    ) -> dict[uuid.UUID, tuple[datetime, ...]]:
        rows = await self.session.execute(
            text(
                """
                -- rule-engine transitions
                SELECT t.rule_id, t.observed_at
                FROM rule_state_transition t
                JOIN analytics_rule r ON r.id = t.rule_id
                WHERE r.instance_id = :instance_id
                  AND t.observed_at >= :load_from
                  AND t.observed_at <= :newest_end
                """
            ),
            {
                "instance_id": instance_id,
                "load_from": load_from,
                "newest_end": newest_end,
            },
        )
        grouped: dict[uuid.UUID, list[datetime]] = {}
        for rule_id, observed_at in rows:
            grouped.setdefault(_uuid(rule_id), []).append(_aware(observed_at))
        return {key: tuple(value) for key, value in grouped.items()}

    async def _load_watermark(self, instance_id: uuid.UUID) -> datetime | None:
        rows = await self.session.execute(
            text(
                """
                -- rule-engine watermark
                SELECT watermark_at
                FROM collection_watermark
                WHERE instance_id = :instance_id
                  AND collector = :collector
                """
            ),
            {"instance_id": instance_id, "collector": RULE_INVENTORY},
        )
        return _optional_dt(rows.scalar())

    async def _load_metrics(
        self, instance_id: uuid.UUID, load_from: datetime, newest: datetime
    ) -> dict[tuple[uuid.UUID, datetime], int]:
        """Metric rows in the loaded span. A missing row stays missing (None).

        Silence treats a COMPLETE window with no row as a proven zero. Passing 0
        would make that window a measured count and a false healthy verdict.
        Storm's own summary coalesces a COMPLETE gap to 0 so a zero median exists.
        """
        rows = await self.session.execute(
            text(
                """
                -- rule-engine silence
                SELECT m.rule_id, m.bucket_start, m.matched_events
                FROM rule_match_metric m
                JOIN analytics_rule r ON r.id = m.rule_id
                WHERE r.instance_id = :instance_id
                  AND m.bucket_start >= :load_from
                  AND m.bucket_start <= :newest
                """
            ),
            {"instance_id": instance_id, "load_from": load_from, "newest": newest},
        )
        return {
            (_uuid(rule_id), _aware(bucket)): int(matched)
            for rule_id, bucket, matched in rows
            if matched is not None
        }

    async def _load_cells(
        self,
        instance_id: uuid.UUID,
        lookback_start: datetime,
        newest: datetime,
        newest_end: datetime,
        width: int,
        starts: Sequence[datetime],
        thresholds: RuleThresholds,
    ) -> dict[tuple[uuid.UUID, int, int], RuleCell]:
        """One summary per (rule, weekday, slot). Skipped when the width does not tile a day.

        The skip depends on the width, not on how many rules the instance has, so
        the 100-versus-5,000 count still matches. A non-tiling width makes every
        storm ``INSUFFICIENT_DATA`` before it would have read a cell.
        """
        if width <= 0 or _DAY_SECONDS % width != 0:
            return {}
        rows = (
            await self.session.execute(
                _statement(
                    """
                    -- rule-engine storm
                    WITH storm_rules AS (
                        SELECT DISTINCT m.rule_id
                        FROM rule_match_metric m
                        JOIN analytics_rule r ON r.id = m.rule_id
                        WHERE r.instance_id = :instance_id
                          AND m.bucket_start = ANY(CAST(:starts AS timestamptz[]))
                    ),
                    samples AS (
                        SELECT
                            sr.rule_id,
                            CAST(COALESCE(metric.matched_events, 0) AS double precision)
                                AS value,
                            CAST(
                                EXTRACT(ISODOW FROM w.window_start AT TIME ZONE 'UTC')
                                AS integer
                            ) AS weekday,
                            CAST(FLOOR(
                                (
                                    CAST(EXTRACT(HOUR FROM w.window_start
                                        AT TIME ZONE 'UTC') AS integer) * 3600
                                    + CAST(EXTRACT(MINUTE FROM w.window_start
                                        AT TIME ZONE 'UTC') AS integer) * 60
                                    + CAST(FLOOR(EXTRACT(SECOND FROM w.window_start
                                        AT TIME ZONE 'UTC')) AS integer)
                                ) / :width
                            ) AS integer) AS slot,
                            (
                                CAST(EXTRACT(ISOYEAR FROM w.window_start
                                    AT TIME ZONE 'UTC') AS integer) * 100
                                + CAST(EXTRACT(WEEK FROM w.window_start
                                    AT TIME ZONE 'UTC') AS integer)
                            ) AS week_key,
                            (w.origin = 'HISTORY') AS is_history
                        FROM storm_rules sr
                        JOIN rule_match_window w
                          ON w.instance_id = :instance_id
                         AND w.status = 'COMPLETE'
                         AND w.window_seconds = :width
                         AND w.window_start >= :lookback_start
                         AND w.window_start <= :newest
                         AND w.window_start <> ALL(CAST(:starts AS timestamptz[]))
                        LEFT JOIN rule_match_metric metric
                          ON metric.rule_id = sr.rule_id
                         AND metric.bucket_start = w.window_start
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM rule_anomaly incident
                            WHERE incident.rule_id = sr.rule_id
                              AND incident.detector_type = 'RULE_STORM'
                              AND w.window_start >= LEAST(
                                    incident.detected_at,
                                    COALESCE(incident.anomaly_start, incident.detected_at)
                                  )
                              AND w.window_start <= COALESCE(
                                    incident.resolved_at,
                                    incident.anomaly_end,
                                    :newest_end
                                  )
                        )
                    ),
                    medians AS (
                        SELECT
                            rule_id, weekday, slot,
                            percentile_cont(0.5) WITHIN GROUP (ORDER BY value) AS median,
                            percentile_cont(0.05) WITHIN GROUP (ORDER BY value) AS p05,
                            percentile_cont(0.95) WITHIN GROUP (ORDER BY value) AS p95,
                            count(*) AS sample_count,
                            count(DISTINCT week_key) AS visits,
                            count(*) FILTER (WHERE is_history) AS history_samples
                        FROM samples
                        GROUP BY rule_id, weekday, slot
                    ),
                    devs AS (
                        SELECT
                            samples.rule_id, samples.weekday, samples.slot,
                            abs(samples.value - medians.median) AS dev
                        FROM samples
                        JOIN medians USING (rule_id, weekday, slot)
                    )
                    SELECT
                        medians.rule_id, medians.weekday, medians.slot,
                        medians.median, medians.p05, medians.p95,
                        medians.sample_count, medians.visits, medians.history_samples,
                        percentile_cont(0.5) WITHIN GROUP (ORDER BY devs.dev) AS mad
                    FROM medians
                    JOIN devs USING (rule_id, weekday, slot)
                    GROUP BY
                        medians.rule_id, medians.weekday, medians.slot,
                        medians.median, medians.p05, medians.p95,
                        medians.sample_count, medians.visits, medians.history_samples
                    """,
                    starts=_TIMESTAMPS,
                ),
                {
                    "instance_id": instance_id,
                    "starts": list(starts),
                    "width": width,
                    "lookback_start": lookback_start,
                    "newest": newest,
                    "newest_end": newest_end,
                },
            )
        ).mappings()
        cells: dict[tuple[uuid.UUID, int, int], RuleCell] = {}
        for row in rows:
            sample_count = int(row["sample_count"])
            visits = int(row["visits"])
            summary = CellSummary(
                median=float(row["median"]),
                mad=float(row["mad"] or 0.0),
                p05=float(row["p05"]),
                p95=float(row["p95"]),
                sample_count=sample_count,
                is_reliable=sample_count >= thresholds.storm_min_samples,
                observations=[],
            )
            weekday = int(row["weekday"])
            slot = int(row["slot"])
            cells[(_uuid(row["rule_id"]), weekday, slot)] = RuleCell(
                weekday=weekday,
                slot=slot,
                summary=summary,
                visits=visits,
                is_reliable=summary.is_reliable and visits >= thresholds.storm_min_visits,
                history_samples=int(row["history_samples"] or 0),
            )
        return cells

    async def _load_states(self, instance_id: uuid.UUID, batch: _Batch) -> None:
        rows = await self.session.scalars(
            select(RuleDetectorState)
            .join(AnalyticsRule, AnalyticsRule.id == RuleDetectorState.rule_id)
            .where(AnalyticsRule.instance_id == instance_id)
        )
        for row in rows:
            state = _State(
                id=row.id,
                rule_id=row.rule_id,
                detector=str(row.detector_type),
                consecutive_anomalous=row.consecutive_anomalous,
                consecutive_healthy=row.consecutive_healthy,
                state=AnomalyState(str(row.state)),
                last_interval_start=_optional_dt(row.last_interval_start),
                active_anomaly_id=row.active_anomaly_id,
                open_anomaly_id=row.open_anomaly_id,
            )
            batch.states[(state.rule_id, state.detector)] = state

    async def _load_open_incidents(self, instance_id: uuid.UUID, batch: _Batch) -> None:
        rows = await self.session.scalars(
            select(RuleAnomaly)
            .join(AnalyticsRule, AnalyticsRule.id == RuleAnomaly.rule_id)
            .where(
                AnalyticsRule.instance_id == instance_id,
                RuleAnomaly.state.in_([item.value for item in ACTIVE_ANOMALY_STATES]),
            )
        )
        for row in rows:
            incident = _incident_from_row(row)
            batch.incidents[incident.id] = incident
            batch.open_by[(incident.rule_id, incident.detector)] = incident

    def _judge_detector(
        self,
        instance_id: uuid.UUID,
        instance_name: str,
        rule: _RuleRow,
        detector: str,
        starts: Sequence[datetime],
        series: _Series,
        cells: dict[tuple[uuid.UUID, int, int], RuleCell],
        watermark: datetime | None,
        thresholds: RuleThresholds,
        width: int,
        batch: _Batch,
        report: RuleEvaluationReport,
    ) -> None:
        lag = self.settings.rule_match_settle_lag_seconds
        settled_until = self._clock() - timedelta(seconds=lag)
        for start in starts:
            state = _state_for(batch, rule.id, detector)
            if (
                state.last_interval_start is not None
                and state.last_interval_start >= start
            ):
                continue
            incident = batch.open_by.get((rule.id, detector))
            previous = state.state
            incident_open = previous in (
                AnomalyState.OPEN,
                AnomalyState.RECOVERING,
            ) and incident is not None
            streak = incident.anomaly_start if incident_open and incident is not None else None
            evidence = self._evidence(
                rule,
                detector,
                start,
                series,
                cells,
                watermark,
                thresholds,
                width,
                settled_until,
                incident_open=incident_open,
                streak=streak,
            )
            self._advance(
                instance_id,
                instance_name,
                rule,
                detector,
                start,
                evidence,
                state,
                previous,
                incident,
                batch,
                report,
            )

    def _evidence(
        self,
        rule: _RuleRow,
        detector: str,
        start: datetime,
        series: _Series,
        cells: dict[tuple[uuid.UUID, int, int], RuleCell],
        watermark: datetime | None,
        thresholds: RuleThresholds,
        width: int,
        settled_until: datetime,
        *,
        incident_open: bool,
        streak: datetime | None,
    ) -> AnomalyEvidence[RuleAnomalyType]:
        if detector == RuleAnomalyType.RULE_SILENCE.value:
            return detect_silence(
                SilenceContext(
                    subject=rule.subject,
                    windows=series.facts,
                    window_seconds=width,
                    judged_start=start,
                    settled_until=settled_until,
                    inventory_watermark=watermark,
                    incident_open=incident_open,
                    incident_streak_start=streak,
                    thresholds=thresholds,
                )
            )
        key = slot_of(start, width)
        cell = cells.get((rule.id, key[0], key[1])) if key is not None else None
        return detect_storm(
            StormContext(
                subject=rule.subject,
                window=series.by_start.get(start),
                window_seconds=width,
                judged_start=start,
                settled_until=settled_until,
                inventory_watermark=watermark,
                cell=cell,
                thresholds=thresholds,
            )
        )

    def _advance(
        self,
        instance_id: uuid.UUID,
        instance_name: str,
        rule: _RuleRow,
        detector: str,
        start: datetime,
        evidence: AnomalyEvidence[RuleAnomalyType],
        state: _State,
        previous: AnomalyState,
        incident: _Incident | None,
        batch: _Batch,
        report: RuleEvaluationReport,
    ) -> None:
        state.consecutive_anomalous, state.consecutive_healthy = advance_counters(
            evidence.signal, state.consecutive_anomalous, state.consecutive_healthy
        )
        state.last_interval_start = start
        state.dirty = True
        decision = next_state(
            LifecycleInput(
                state=previous,
                signal=evidence.signal,
                consecutive_anomalous=state.consecutive_anomalous,
                consecutive_healthy=state.consecutive_healthy,
                open_after=self._open_after(detector),
                resolve_after=self.settings.anomaly_resolve_after_intervals,
                insufficient_data=evidence.insufficient_data,
            )
        )
        target = decision.state
        if target is previous:
            if (
                incident is not None
                and previous is AnomalyState.OPEN
                and evidence.is_anomalous
            ):
                _touch(incident, state, evidence)
                if self._alerting:
                    batch.alerts.append(
                        _open_action(rule, detector, evidence, start, incident.id)
                    )
            return
        state.state = target
        if target in (AnomalyState.CANDIDATE, AnomalyState.OPEN) and incident is None:
            incident = _new_incident(
                instance_id, rule, detector, evidence, state, target, self._clock(),
                self.settings.anomaly_policy_version,
            )
            batch.incidents[incident.id] = incident
            batch.open_by[(rule.id, detector)] = incident
            state.active_anomaly_id = incident.id
        if incident is not None:
            incident.state = target.value
            incident.dirty = True
            batch.transitions.append(
                _transition(
                    incident.id,
                    previous,
                    target,
                    start,
                    decision.reason,
                    decision.message.to_wire(),
                    evidence,
                    self._clock(),
                )
            )
        if target is AnomalyState.OPEN:
            assert incident is not None
            if incident.opened_at is None:
                incident.opened_at = self._clock()
            incident.anomaly_end = None
            state.open_anomaly_id = incident.id
            _touch(incident, state, evidence)
            if previous not in (AnomalyState.OPEN, AnomalyState.RECOVERING):
                report.incidents_opened += 1
            if self._alerting:
                batch.alerts.append(
                    _open_action(rule, detector, evidence, start, incident.id)
                )
            return
        if target is AnomalyState.RECOVERING and incident is not None:
            incident.anomaly_end = start
            return
        if target is AnomalyState.RESOLVED:
            if incident is not None:
                incident.resolved_at = self._clock()
                if incident.anomaly_end is None:
                    incident.anomaly_end = start
            state.active_anomaly_id = None
            state.open_anomaly_id = None
            batch.open_by.pop((rule.id, detector), None)
            report.incidents_resolved += 1
            if self._alerting and previous in (AnomalyState.OPEN, AnomalyState.RECOVERING):
                batch.alerts.append(
                    _AlertResolve(rule.id, detector, decision.reason)
                )
            return
        if target is AnomalyState.NORMAL:
            if incident is not None:
                incident.anomaly_end = start
            state.active_anomaly_id = None
            state.open_anomaly_id = None
            batch.open_by.pop((rule.id, detector), None)

    def _resolve_subject(
        self,
        rule: _RuleRow,
        reason: str,
        batch: _Batch,
        newest: datetime,
        report: RuleEvaluationReport,
    ) -> None:
        """Bulk-resolve every live incident of a rule that is no longer a subject.

        A disabled resolve does not open an alert. The alert resolve, when the
        family switch is on, carries only the fingerprint and this reason.
        """
        for detector in _DETECTORS:
            incident = batch.open_by.get((rule.id, detector))
            state = batch.states.get((rule.id, detector))
            previous = state.state if state is not None else AnomalyState.NORMAL
            if incident is None and previous not in ACTIVE_ANOMALY_STATES:
                continue
            if state is None:
                state = _fresh_state(rule.id, detector)
                batch.states[(rule.id, detector)] = state
            state.state = AnomalyState.RESOLVED
            state.consecutive_anomalous = 0
            state.consecutive_healthy = 0
            state.active_anomaly_id = None
            state.open_anomaly_id = None
            state.last_interval_start = newest
            state.dirty = True
            if incident is not None:
                incident.state = AnomalyState.RESOLVED.value
                incident.resolved_at = self._clock()
                if incident.anomaly_end is None:
                    incident.anomaly_end = newest
                incident.dirty = True
                batch.transitions.append(
                    _Transition(
                        id=uuid.uuid4(),
                        anomaly_id=incident.id,
                        from_state=previous.value,
                        to_state=AnomalyState.RESOLVED.value,
                        occurred_at=self._clock(),
                        bucket_start=newest,
                        reason=reason,
                        reason_i18n=None,
                        observed=incident.observed_value,
                        expected=incident.expected_value,
                    )
                )
                batch.open_by.pop((rule.id, detector), None)
                report.incidents_resolved += 1
            if self._alerting and previous in (AnomalyState.OPEN, AnomalyState.RECOVERING):
                batch.alerts.append(_AlertResolve(rule.id, detector, reason))

    async def _write(self, batch: _Batch) -> None:
        dirty_states = [item for item in batch.states.values() if item.dirty]
        dirty_incidents = [item for item in batch.incidents.values() if item.dirty]
        if dirty_states:
            await self._write_states(dirty_states)
        if dirty_incidents:
            await self._write_incidents(dirty_incidents)
        if batch.transitions:
            await self._write_transitions(batch.transitions)

    async def _write_states(self, rows: Sequence[_State]) -> None:
        await self.session.execute(
            _statement(
                """
                -- rule-engine state-upsert
                INSERT INTO rule_detector_state (
                    id, rule_id, detector_type, consecutive_anomalous,
                    consecutive_healthy, state, last_interval_start,
                    active_anomaly_id, open_anomaly_id
                )
                SELECT
                    pending.id, pending.rule_id, pending.detector_type,
                    pending.consecutive_anomalous, pending.consecutive_healthy,
                    pending.state, pending.last_interval_start,
                    pending.active_anomaly_id, pending.open_anomaly_id
                FROM unnest(
                    CAST(:ids AS uuid[]),
                    CAST(:rule_ids AS uuid[]),
                    CAST(:detectors AS text[]),
                    CAST(:anomalous AS integer[]),
                    CAST(:healthy AS integer[]),
                    CAST(:states AS text[]),
                    CAST(:last_starts AS timestamptz[]),
                    CAST(:active_ids AS uuid[]),
                    CAST(:open_ids AS uuid[])
                ) AS pending(
                    id, rule_id, detector_type, consecutive_anomalous,
                    consecutive_healthy, state, last_interval_start,
                    active_anomaly_id, open_anomaly_id
                )
                ON CONFLICT ON CONSTRAINT uq_rule_detector_state_rule DO UPDATE SET
                    consecutive_anomalous = EXCLUDED.consecutive_anomalous,
                    consecutive_healthy = EXCLUDED.consecutive_healthy,
                    state = EXCLUDED.state,
                    last_interval_start = EXCLUDED.last_interval_start,
                    active_anomaly_id = EXCLUDED.active_anomaly_id,
                    open_anomaly_id = EXCLUDED.open_anomaly_id,
                    updated_at = now()
                """,
                ids=_UUIDS,
                rule_ids=_UUIDS,
                detectors=_TEXTS,
                anomalous=_INTS,
                healthy=_INTS,
                states=_TEXTS,
                last_starts=_TIMESTAMPS,
                active_ids=_UUIDS,
                open_ids=_UUIDS,
            ),
            {
                "ids": [row.id for row in rows],
                "rule_ids": [row.rule_id for row in rows],
                "detectors": [row.detector for row in rows],
                "anomalous": [row.consecutive_anomalous for row in rows],
                "healthy": [row.consecutive_healthy for row in rows],
                "states": [row.state.value for row in rows],
                "last_starts": [row.last_interval_start for row in rows],
                "active_ids": [row.active_anomaly_id for row in rows],
                "open_ids": [row.open_anomaly_id for row in rows],
            },
        )

    async def _write_incidents(self, rows: Sequence[_Incident]) -> None:
        await self.session.execute(
            _statement(
                """
                -- rule-engine anomaly-upsert
                INSERT INTO rule_anomaly (
                    id, rule_id, qradar_id, is_building_block, detector_type,
                    fingerprint, severity, state, detected_at, opened_at,
                    anomaly_start, anomaly_end, resolved_at, observed_value,
                    expected_value, deviation_score, deviation_ratio, robust_z,
                    absolute_delta, confidence, consecutive_buckets, policy_version,
                    explanation, details, evidence_status, suppressed
                )
                SELECT
                    pending.id, pending.rule_id, pending.qradar_id,
                    pending.is_building_block, pending.detector_type,
                    pending.fingerprint, pending.severity, pending.state,
                    pending.detected_at, pending.opened_at, pending.anomaly_start,
                    pending.anomaly_end, pending.resolved_at, pending.observed_value,
                    pending.expected_value, pending.deviation_score,
                    pending.deviation_ratio, pending.robust_z, pending.absolute_delta,
                    pending.confidence, pending.consecutive_buckets,
                    pending.policy_version, pending.explanation,
                    CAST(pending.details AS jsonb), pending.evidence_status,
                    pending.suppressed
                FROM unnest(
                    CAST(:ids AS uuid[]),
                    CAST(:rule_ids AS uuid[]),
                    CAST(:qradar_ids AS integer[]),
                    CAST(:building_blocks AS boolean[]),
                    CAST(:detectors AS text[]),
                    CAST(:fingerprints AS text[]),
                    CAST(:severities AS text[]),
                    CAST(:states AS text[]),
                    CAST(:detected_at AS timestamptz[]),
                    CAST(:opened_at AS timestamptz[]),
                    CAST(:anomaly_start AS timestamptz[]),
                    CAST(:anomaly_end AS timestamptz[]),
                    CAST(:resolved_at AS timestamptz[]),
                    CAST(:observed AS double precision[]),
                    CAST(:expected AS double precision[]),
                    CAST(:deviation_score AS double precision[]),
                    CAST(:deviation_ratio AS double precision[]),
                    CAST(:robust_z AS double precision[]),
                    CAST(:absolute_delta AS double precision[]),
                    CAST(:confidence AS double precision[]),
                    CAST(:consecutive AS integer[]),
                    CAST(:policy_version AS integer[]),
                    CAST(:explanation AS text[]),
                    CAST(:details AS text[]),
                    CAST(:evidence_status AS text[]),
                    CAST(:suppressed AS boolean[])
                ) AS pending(
                    id, rule_id, qradar_id, is_building_block, detector_type,
                    fingerprint, severity, state, detected_at, opened_at,
                    anomaly_start, anomaly_end, resolved_at, observed_value,
                    expected_value, deviation_score, deviation_ratio, robust_z,
                    absolute_delta, confidence, consecutive_buckets, policy_version,
                    explanation, details, evidence_status, suppressed
                )
                ON CONFLICT (id) DO UPDATE SET
                    severity = EXCLUDED.severity,
                    state = EXCLUDED.state,
                    opened_at = EXCLUDED.opened_at,
                    anomaly_start = EXCLUDED.anomaly_start,
                    anomaly_end = EXCLUDED.anomaly_end,
                    resolved_at = EXCLUDED.resolved_at,
                    observed_value = EXCLUDED.observed_value,
                    expected_value = EXCLUDED.expected_value,
                    deviation_score = EXCLUDED.deviation_score,
                    deviation_ratio = EXCLUDED.deviation_ratio,
                    robust_z = EXCLUDED.robust_z,
                    absolute_delta = EXCLUDED.absolute_delta,
                    confidence = EXCLUDED.confidence,
                    consecutive_buckets = EXCLUDED.consecutive_buckets,
                    policy_version = EXCLUDED.policy_version,
                    explanation = EXCLUDED.explanation,
                    details = EXCLUDED.details,
                    updated_at = now()
                """,
                ids=_UUIDS,
                rule_ids=_UUIDS,
                qradar_ids=_INTS,
                building_blocks=_BOOLS,
                detectors=_TEXTS,
                fingerprints=_TEXTS,
                severities=_TEXTS,
                states=_TEXTS,
                detected_at=_TIMESTAMPS,
                opened_at=_TIMESTAMPS,
                anomaly_start=_TIMESTAMPS,
                anomaly_end=_TIMESTAMPS,
                resolved_at=_TIMESTAMPS,
                observed=_FLOATS,
                expected=_FLOATS,
                deviation_score=_FLOATS,
                deviation_ratio=_FLOATS,
                robust_z=_FLOATS,
                absolute_delta=_FLOATS,
                confidence=_FLOATS,
                consecutive=_INTS,
                policy_version=_INTS,
                explanation=_TEXTS,
                details=_TEXTS,
                evidence_status=_TEXTS,
                suppressed=_BOOLS,
            ),
            {
                "ids": [row.id for row in rows],
                "rule_ids": [row.rule_id for row in rows],
                "qradar_ids": [row.qradar_id for row in rows],
                "building_blocks": [row.is_building_block for row in rows],
                "detectors": [row.detector for row in rows],
                "fingerprints": [row.fingerprint for row in rows],
                "severities": [row.severity for row in rows],
                "states": [row.state for row in rows],
                "detected_at": [row.detected_at for row in rows],
                "opened_at": [row.opened_at for row in rows],
                "anomaly_start": [row.anomaly_start for row in rows],
                "anomaly_end": [row.anomaly_end for row in rows],
                "resolved_at": [row.resolved_at for row in rows],
                "observed": [row.observed_value for row in rows],
                "expected": [row.expected_value for row in rows],
                "deviation_score": [row.deviation_score for row in rows],
                "deviation_ratio": [row.deviation_ratio for row in rows],
                "robust_z": [row.robust_z for row in rows],
                "absolute_delta": [row.absolute_delta for row in rows],
                "confidence": [row.confidence for row in rows],
                "consecutive": [row.consecutive_buckets for row in rows],
                "policy_version": [row.policy_version for row in rows],
                "explanation": [row.explanation for row in rows],
                "details": [json.dumps(row.details, default=str) for row in rows],
                "evidence_status": [EvidenceStatus.NOT_REQUESTED.value for _ in rows],
                "suppressed": [False for _ in rows],
            },
        )

    async def _write_transitions(self, rows: Sequence[_Transition]) -> None:
        await self.session.execute(
            _statement(
                """
                -- rule-engine transition-insert
                INSERT INTO rule_anomaly_transition (
                    id, rule_anomaly_id, from_state, to_state, occurred_at,
                    bucket_start, reason, reason_i18n, actor, observed_value,
                    expected_value, observed_measurement
                )
                SELECT
                    pending.id, pending.rule_anomaly_id, pending.from_state,
                    pending.to_state, pending.occurred_at, pending.bucket_start,
                    pending.reason, CAST(pending.reason_i18n AS jsonb),
                    :actor, pending.observed_value, pending.expected_value,
                    :measurement
                FROM unnest(
                    CAST(:ids AS uuid[]),
                    CAST(:anomaly_ids AS uuid[]),
                    CAST(:from_states AS text[]),
                    CAST(:to_states AS text[]),
                    CAST(:occurred_at AS timestamptz[]),
                    CAST(:bucket_start AS timestamptz[]),
                    CAST(:reasons AS text[]),
                    CAST(:reason_i18n AS text[]),
                    CAST(:observed AS double precision[]),
                    CAST(:expected AS double precision[])
                ) AS pending(
                    id, rule_anomaly_id, from_state, to_state, occurred_at,
                    bucket_start, reason, reason_i18n, observed_value, expected_value
                )
                """,
                ids=_UUIDS,
                anomaly_ids=_UUIDS,
                from_states=_TEXTS,
                to_states=_TEXTS,
                occurred_at=_TIMESTAMPS,
                bucket_start=_TIMESTAMPS,
                reasons=_TEXTS,
                reason_i18n=_TEXTS,
                observed=_FLOATS,
                expected=_FLOATS,
            ),
            {
                "actor": ACTOR,
                "measurement": WafMeasurement.EXACT.value,
                "ids": [row.id for row in rows],
                "anomaly_ids": [row.anomaly_id for row in rows],
                "from_states": [row.from_state for row in rows],
                "to_states": [row.to_state for row in rows],
                "occurred_at": [row.occurred_at for row in rows],
                "bucket_start": [row.bucket_start for row in rows],
                "reasons": [row.reason for row in rows],
                "reason_i18n": [
                    json.dumps(row.reason_i18n) if row.reason_i18n is not None else None
                    for row in rows
                ],
                "observed": [row.observed for row in rows],
                "expected": [row.expected for row in rows],
            },
        )

    async def _apply_alerts(
        self,
        instance_id: uuid.UUID,
        instance_name: str,
        actions: Sequence[_AlertOpen | _AlertResolve],
        width: int,
    ) -> None:
        opens = [item for item in actions if isinstance(item, _AlertOpen)]
        allowed: set[int] = set()
        for item in opens:
            mode = await self._policy.mode(
                instance_id=instance_id,
                family=AnomalyFamily.RULE,
                source_id=item.rule_id,
                source_class=None,
                detector=item.detector,
            )
            if mode is not AlertPolicyMode.ALERT:
                continue
            suppression, breakthrough = await self._suppression.stops(
                family=AnomalyFamily.RULE,
                subject_id=item.rule_id,
                detector=item.detector,
                at=self._clock(),
                evidence=item.evidence,
                criticality=None,
                settings=self.settings,
            )
            if suppression is not None and not breakthrough:
                await record_hit(
                    self.session,
                    suppression,
                    family=AnomalyFamily.RULE,
                    anomaly_id=item.incident_id,
                    detector=item.detector,
                    bucket_start=item.window_start,
                    at=self._clock(),
                )
                continue
            allowed.add(id(item))
        kept = [
            item
            for item in actions
            if not isinstance(item, _AlertOpen) or id(item) in allowed
        ]
        for open_items, resolve_items in _rounds(instance_id, kept):
            inputs = [
                _alert_input(instance_id, instance_name, item, width) for item in open_items
            ]
            resolutions = [
                AlertResolution(
                    fingerprint=rule_anomaly_fingerprint(
                        instance_id, item.rule_id, item.detector
                    ),
                    actor=ACTOR,
                    reason=item.reason,
                )
                for item in resolve_items
            ]
            opened, resolved = await self.alerts.apply_bulk(inputs, resolutions)
            if self._enqueuer is None:
                continue
            for result in [*opened, *resolved]:
                if result is not None and result.transition is not None:
                    await self._enqueuer.enqueue(result.alert, result.transition)

    async def read_rule(self, rule_id: uuid.UUID) -> RuleSectionRead | None:
        """The stored series of one rule, and the verdict each detector returns.

        Same pure functions as ``_evidence``, on a series loaded for this rule
        alone. The batch statements in ``evaluate`` are not used and are not
        changed. A missing rule is None. No window means no verdict: the caller
        says collection has not recorded one.
        """
        loaded = await self._load_one_rule(rule_id)
        if loaded is None:
            return None
        rule, instance_id = loaded
        thresholds = self._thresholds()
        windows = await self._load_instance_windows(instance_id)
        incidents = await self._load_rule_incidents(rule_id)
        if not windows:
            return RuleSectionRead(
                rule_id=rule.id,
                subject=rule.subject,
                thresholds=thresholds,
                watermark=None,
                any_window=False,
                judged_start=None,
                judged_through=None,
                silence=None,
                storm=None,
                incidents=incidents,
            )
        now = _aware(self._clock())
        width = self.settings.rule_match_window_seconds
        step = timedelta(seconds=width)
        settled_until = now - timedelta(seconds=self.settings.rule_match_settle_lag_seconds)
        judged_start, judged_through = _newest_window(windows, step, settled_until)
        assert judged_start is not None
        margin = timedelta(seconds=width * thresholds.silence_windows)
        lookback_start = judged_start - timedelta(days=self.settings.rule_storm_lookback_days)
        span_start = judged_start - timedelta(days=thresholds.silence_baseline_days) - margin
        load_from = min(lookback_start, span_start)
        judged_end = judged_start + step
        transitions = await self._load_rule_transitions(rule.id, load_from, judged_end)
        rule = _with_transitions(rule, transitions)
        watermark = await self._load_watermark(instance_id)
        metrics = await self._load_rule_metrics(rule.id, load_from, judged_start)
        in_span = [
            window
            for window in windows
            if load_from <= window.start <= judged_start
        ]
        series = _series_for(rule.id, in_span, metrics)
        cells = await self._load_rule_cells(
            instance_id,
            rule.id,
            lookback_start,
            judged_start,
            judged_end,
            width,
            thresholds,
        )
        silence_row = _open_for(incidents, RuleAnomalyType.RULE_SILENCE.value)
        storm_row = _open_for(incidents, RuleAnomalyType.RULE_STORM.value)
        silence = self._evidence(
            rule,
            RuleAnomalyType.RULE_SILENCE.value,
            judged_start,
            series,
            cells,
            watermark,
            thresholds,
            width,
            settled_until,
            incident_open=silence_row is not None,
            streak=None if silence_row is None else _optional_dt(silence_row.anomaly_start),
        )
        storm = self._evidence(
            rule,
            RuleAnomalyType.RULE_STORM.value,
            judged_start,
            series,
            cells,
            watermark,
            thresholds,
            width,
            settled_until,
            incident_open=storm_row is not None,
            streak=None if storm_row is None else _optional_dt(storm_row.anomaly_start),
        )
        return RuleSectionRead(
            rule_id=rule.id,
            subject=rule.subject,
            thresholds=thresholds,
            watermark=watermark,
            any_window=True,
            judged_start=judged_start,
            judged_through=judged_through,
            silence=silence,
            storm=storm,
            incidents=incidents,
        )

    async def _load_one_rule(
        self, rule_id: uuid.UUID
    ) -> tuple[_RuleRow, uuid.UUID] | None:
        """The batch rule select, restricted to one id. The batch text is untouched."""
        rows = (
            await self.session.execute(
                text(
                    """
                    -- rule-section rule
                    SELECT
                        r.instance_id,
                        r.id, r.name, r.qradar_id, r.rule_type, r.enabled,
                        r.is_building_block, r.health_monitoring_enabled,
                        r.first_seen_at, r.qradar_created_at, r.qradar_modified_at,
                        COALESCE(dep.disabled_ids, ARRAY[]::integer[]) AS disabled_ids,
                        COALESCE(dep.unobserved_ids, ARRAY[]::integer[]) AS unobserved_ids,
                        COALESCE(dep.dependency_count, 0) AS dependency_count
                    FROM analytics_rule r
                    LEFT JOIN LATERAL (
                        SELECT
                            array_agg(x.qid) FILTER (WHERE x.disabled) AS disabled_ids,
                            array_agg(x.qid) FILTER (WHERE x.unobserved) AS unobserved_ids,
                            count(*) AS dependency_count
                        FROM (
                            SELECT
                                CASE
                                    WHEN d.target_ref ~ '^[0-9]+$'
                                    THEN CAST(d.target_ref AS integer)
                                    ELSE -1
                                END AS qid,
                                (tgt.id IS NOT NULL AND tgt.enabled IS FALSE) AS disabled,
                                (tgt.id IS NULL) AS unobserved
                            FROM rule_dependency d
                            LEFT JOIN analytics_rule tgt
                              ON tgt.instance_id = r.instance_id
                             AND tgt.qradar_id = CASE
                                    WHEN d.target_ref ~ '^[0-9]+$'
                                    THEN CAST(d.target_ref AS integer)
                                    ELSE NULL
                                 END
                            WHERE d.rule_id = r.id
                              AND d.source = 'EXPLICIT'
                              AND d.kind = 'BUILDING_BLOCK'
                        ) x
                    ) dep ON true
                    WHERE r.id = :rule_id
                    """
                ),
                {"rule_id": rule_id},
            )
        ).mappings()
        row = rows.first()
        if row is None:
            return None
        return (
            _RuleRow(
                id=_uuid(row["id"]),
                name=str(row["name"]),
                subject=RuleSubject(
                    qradar_id=int(row["qradar_id"]),
                    rule_type=row["rule_type"] if row["rule_type"] is not None else None,
                    enabled=bool(row["enabled"]),
                    is_building_block=bool(row["is_building_block"]),
                    health_monitoring_enabled=bool(row["health_monitoring_enabled"]),
                    first_seen_at=_optional_dt(row["first_seen_at"]),
                    qradar_created_at=_optional_dt(row["qradar_created_at"]),
                    qradar_modified_at=_optional_dt(row["qradar_modified_at"]),
                    disabled_dependencies=_ints(row["disabled_ids"]),
                    unobserved_dependencies=_ints(row["unobserved_ids"]),
                    dependency_count=int(row["dependency_count"] or 0),
                ),
            ),
            _uuid(row["instance_id"]),
        )

    async def _load_instance_windows(self, instance_id: uuid.UUID) -> list[_WindowRow]:
        """Every recorded window for the instance. One select, however many rows."""
        rows = await self.session.execute(
            text(
                """
                -- rule-section windows
                SELECT window_start, window_seconds, status, origin, unknown_ids
                FROM rule_match_window
                WHERE instance_id = :instance_id
                ORDER BY window_start
                """
            ),
            {"instance_id": instance_id},
        )
        return [
            _WindowRow(
                start=_aware(row[0]),
                window_seconds=int(row[1]),
                status=str(row[2]),
                origin=str(row[3]),
                unknown_ids=int(row[4]),
            )
            for row in rows
        ]

    async def _load_rule_transitions(
        self, rule_id: uuid.UUID, load_from: datetime, newest_end: datetime
    ) -> tuple[datetime, ...]:
        rows = await self.session.execute(
            text(
                """
                -- rule-section transitions
                SELECT observed_at
                FROM rule_state_transition
                WHERE rule_id = :rule_id
                  AND observed_at >= :load_from
                  AND observed_at <= :newest_end
                """
            ),
            {"rule_id": rule_id, "load_from": load_from, "newest_end": newest_end},
        )
        return tuple(_aware(row[0]) for row in rows if row[0] is not None)

    async def _load_rule_metrics(
        self, rule_id: uuid.UUID, load_from: datetime, newest: datetime
    ) -> dict[tuple[uuid.UUID, datetime], int]:
        """This rule's metric rows. A missing row stays missing, as in the batch load."""
        rows = await self.session.execute(
            text(
                """
                -- rule-section metrics
                SELECT bucket_start, matched_events
                FROM rule_match_metric
                WHERE rule_id = :rule_id
                  AND bucket_start >= :load_from
                  AND bucket_start <= :newest
                """
            ),
            {"rule_id": rule_id, "load_from": load_from, "newest": newest},
        )
        return {
            (rule_id, _aware(bucket)): int(matched)
            for bucket, matched in rows
            if matched is not None
        }

    async def _load_rule_cells(
        self,
        instance_id: uuid.UUID,
        rule_id: uuid.UUID,
        lookback_start: datetime,
        newest: datetime,
        newest_end: datetime,
        width: int,
        thresholds: RuleThresholds,
    ) -> dict[tuple[uuid.UUID, int, int], RuleCell]:
        """The batch storm summary, for this rule only.

        Skipped when the width does not tile a day, the same condition as
        ``_load_cells``, so a storm on that width is ``width_does_not_tile``
        before it would read a cell. The batch statement is not this one.
        """
        if width <= 0 or _DAY_SECONDS % width != 0:
            return {}
        rows = (
            await self.session.execute(
                _statement(
                    """
                    -- rule-section storm
                    WITH storm_rules AS (
                        SELECT r.id AS rule_id
                        FROM analytics_rule r
                        WHERE r.id = :rule_id
                          AND EXISTS (
                              SELECT 1
                              FROM rule_match_metric m
                              WHERE m.rule_id = r.id
                                AND m.bucket_start = ANY(CAST(:starts AS timestamptz[]))
                          )
                    ),
                    samples AS (
                        SELECT
                            sr.rule_id,
                            CAST(COALESCE(metric.matched_events, 0) AS double precision)
                                AS value,
                            CAST(
                                EXTRACT(ISODOW FROM w.window_start AT TIME ZONE 'UTC')
                                AS integer
                            ) AS weekday,
                            CAST(FLOOR(
                                (
                                    CAST(EXTRACT(HOUR FROM w.window_start
                                        AT TIME ZONE 'UTC') AS integer) * 3600
                                    + CAST(EXTRACT(MINUTE FROM w.window_start
                                        AT TIME ZONE 'UTC') AS integer) * 60
                                    + CAST(FLOOR(EXTRACT(SECOND FROM w.window_start
                                        AT TIME ZONE 'UTC')) AS integer)
                                ) / :width
                            ) AS integer) AS slot,
                            (
                                CAST(EXTRACT(ISOYEAR FROM w.window_start
                                    AT TIME ZONE 'UTC') AS integer) * 100
                                + CAST(EXTRACT(WEEK FROM w.window_start
                                    AT TIME ZONE 'UTC') AS integer)
                            ) AS week_key,
                            (w.origin = 'HISTORY') AS is_history
                        FROM storm_rules sr
                        JOIN rule_match_window w
                          ON w.instance_id = :instance_id
                         AND w.status = 'COMPLETE'
                         AND w.window_seconds = :width
                         AND w.window_start >= :lookback_start
                         AND w.window_start <= :newest
                         AND w.window_start <> ALL(CAST(:starts AS timestamptz[]))
                        LEFT JOIN rule_match_metric metric
                          ON metric.rule_id = sr.rule_id
                         AND metric.bucket_start = w.window_start
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM rule_anomaly incident
                            WHERE incident.rule_id = sr.rule_id
                              AND incident.detector_type = 'RULE_STORM'
                              AND w.window_start >= LEAST(
                                    incident.detected_at,
                                    COALESCE(incident.anomaly_start, incident.detected_at)
                                  )
                              AND w.window_start <= COALESCE(
                                    incident.resolved_at,
                                    incident.anomaly_end,
                                    :newest_end
                                  )
                        )
                    ),
                    medians AS (
                        SELECT
                            rule_id, weekday, slot,
                            percentile_cont(0.5) WITHIN GROUP (ORDER BY value) AS median,
                            percentile_cont(0.05) WITHIN GROUP (ORDER BY value) AS p05,
                            percentile_cont(0.95) WITHIN GROUP (ORDER BY value) AS p95,
                            count(*) AS sample_count,
                            count(DISTINCT week_key) AS visits,
                            count(*) FILTER (WHERE is_history) AS history_samples
                        FROM samples
                        GROUP BY rule_id, weekday, slot
                    ),
                    devs AS (
                        SELECT
                            samples.rule_id, samples.weekday, samples.slot,
                            abs(samples.value - medians.median) AS dev
                        FROM samples
                        JOIN medians USING (rule_id, weekday, slot)
                    )
                    SELECT
                        medians.rule_id, medians.weekday, medians.slot,
                        medians.median, medians.p05, medians.p95,
                        medians.sample_count, medians.visits, medians.history_samples,
                        percentile_cont(0.5) WITHIN GROUP (ORDER BY devs.dev) AS mad
                    FROM medians
                    JOIN devs USING (rule_id, weekday, slot)
                    GROUP BY
                        medians.rule_id, medians.weekday, medians.slot,
                        medians.median, medians.p05, medians.p95,
                        medians.sample_count, medians.visits, medians.history_samples
                    """,
                    starts=_TIMESTAMPS,
                ),
                {
                    "instance_id": instance_id,
                    "rule_id": rule_id,
                    "starts": [newest],
                    "width": width,
                    "lookback_start": lookback_start,
                    "newest": newest,
                    "newest_end": newest_end,
                },
            )
        ).mappings()
        cells: dict[tuple[uuid.UUID, int, int], RuleCell] = {}
        for row in rows:
            sample_count = int(row["sample_count"])
            visits = int(row["visits"])
            summary = CellSummary(
                median=float(row["median"]),
                mad=float(row["mad"] or 0.0),
                p05=float(row["p05"]),
                p95=float(row["p95"]),
                sample_count=sample_count,
                is_reliable=sample_count >= thresholds.storm_min_samples,
                observations=[],
            )
            weekday = int(row["weekday"])
            slot = int(row["slot"])
            cells[(_uuid(row["rule_id"]), weekday, slot)] = RuleCell(
                weekday=weekday,
                slot=slot,
                summary=summary,
                visits=visits,
                is_reliable=summary.is_reliable and visits >= thresholds.storm_min_visits,
                history_samples=int(row["history_samples"] or 0),
            )
        return cells

    async def _load_rule_incidents(self, rule_id: uuid.UUID) -> tuple[RuleAnomaly, ...]:
        """Open incidents, and the single most recent closed one. One select."""
        latest_closed = (
            select(RuleAnomaly.id)
            .where(
                RuleAnomaly.rule_id == rule_id,
                RuleAnomaly.state == AnomalyState.RESOLVED.value,
            )
            .order_by(
                func.coalesce(RuleAnomaly.resolved_at, RuleAnomaly.detected_at).desc(),
                RuleAnomaly.id.desc(),
            )
            .limit(1)
            .scalar_subquery()
        )
        rows = (
            await self.session.scalars(
                select(RuleAnomaly).where(
                    RuleAnomaly.rule_id == rule_id,
                    or_(
                        RuleAnomaly.state.in_(
                            [AnomalyState.OPEN.value, AnomalyState.RECOVERING.value]
                        ),
                        RuleAnomaly.id == latest_closed,
                    ),
                )
            )
        ).all()
        opens = [
            row
            for row in rows
            if _anomaly_state(row) in (AnomalyState.OPEN.value, AnomalyState.RECOVERING.value)
        ]
        closed = [row for row in rows if _anomaly_state(row) == AnomalyState.RESOLVED.value]
        opens.sort(key=lambda row: _aware(row.detected_at), reverse=True)
        closed.sort(
            key=lambda row: _aware(row.resolved_at or row.detected_at), reverse=True
        )
        return (*opens, *closed[:1])


def _anomaly_state(row: RuleAnomaly) -> str:
    state = row.state
    if isinstance(state, AnomalyState):
        return state.value
    return str(state)


def _open_for(incidents: Sequence[RuleAnomaly], detector: str) -> RuleAnomaly | None:
    """The live (open or recovering) incident for one detector, if the engine would hold it."""
    for row in incidents:
        kind = row.detector_type
        name = kind.value if isinstance(kind, RuleAnomalyType) else str(kind)
        if name == detector and _anomaly_state(row) in (
            AnomalyState.OPEN.value,
            AnomalyState.RECOVERING.value,
        ):
            return row
    return None


def _newest_window(
    windows: Sequence[_WindowRow], step: timedelta, settled_until: datetime
) -> tuple[datetime, datetime | None]:
    """(start to judge, end of that window if it has settled).

    The judged start is the newest settled window, or the newest recorded one
    when nothing has settled yet. ``judged_through`` stays None in that case.
    """
    settled = [window for window in windows if window.start + step <= _aware(settled_until)]
    if settled:
        chosen = max(settled, key=lambda window: window.start)
        return chosen.start, chosen.start + step
    chosen = max(windows, key=lambda window: window.start)
    return chosen.start, None


def _with_transitions(rule: _RuleRow, transitions: tuple[datetime, ...]) -> _RuleRow:
    subject = rule.subject
    if subject.transitions == transitions:
        return rule
    return _RuleRow(
        id=rule.id,
        name=rule.name,
        subject=RuleSubject(
            qradar_id=subject.qradar_id,
            rule_type=subject.rule_type,
            enabled=subject.enabled,
            is_building_block=subject.is_building_block,
            health_monitoring_enabled=subject.health_monitoring_enabled,
            first_seen_at=subject.first_seen_at,
            qradar_created_at=subject.qradar_created_at,
            qradar_modified_at=subject.qradar_modified_at,
            transitions=transitions,
            disabled_dependencies=subject.disabled_dependencies,
            unobserved_dependencies=subject.unobserved_dependencies,
            dependency_count=subject.dependency_count,
        ),
    )


def _series_for(
    rule_id: uuid.UUID,
    windows: Sequence[_WindowRow],
    metrics: dict[tuple[uuid.UUID, datetime], int],
) -> _Series:
    facts = [
        WindowFact(
            start=window.start,
            window_seconds=window.window_seconds,
            status=window.status,
            matched_events=metrics.get((rule_id, window.start)),
            origin=window.origin,
            unknown_ids=window.unknown_ids,
        )
        for window in windows
    ]
    return _Series(facts=facts, by_start={fact.start: fact for fact in facts})


def _state_for(batch: _Batch, rule_id: uuid.UUID, detector: str) -> _State:
    key = (rule_id, detector)
    state = batch.states.get(key)
    if state is None:
        state = _fresh_state(rule_id, detector)
        batch.states[key] = state
    return state


def _incident_from_row(row: RuleAnomaly) -> _Incident:
    return _Incident(
        id=row.id,
        rule_id=row.rule_id,
        qradar_id=row.qradar_id,
        is_building_block=row.is_building_block,
        detector=str(row.detector_type),
        fingerprint=row.fingerprint,
        severity=str(row.severity),
        state=str(row.state),
        detected_at=_aware(row.detected_at),
        opened_at=_optional_dt(row.opened_at),
        anomaly_start=_optional_dt(row.anomaly_start),
        anomaly_end=_optional_dt(row.anomaly_end),
        resolved_at=_optional_dt(row.resolved_at),
        observed_value=row.observed_value,
        expected_value=row.expected_value,
        deviation_score=row.deviation_score,
        deviation_ratio=row.deviation_ratio,
        robust_z=row.robust_z,
        absolute_delta=row.absolute_delta,
        confidence=row.confidence,
        consecutive_buckets=row.consecutive_buckets,
        policy_version=row.policy_version,
        explanation=row.explanation,
        details=dict(row.details or {}),
    )


def _touch(
    incident: _Incident, state: _State, evidence: AnomalyEvidence[RuleAnomalyType]
) -> None:
    measured = _measurements(evidence)
    incident.severity = evidence.severity.value
    incident.explanation = evidence.reason
    incident.consecutive_buckets = state.consecutive_anomalous
    incident.details = _jsonable(evidence.to_dict())
    incident.observed_value = measured["observed_value"]
    incident.expected_value = measured["expected_value"]
    incident.deviation_score = measured["deviation_score"]
    incident.deviation_ratio = measured["deviation_ratio"]
    incident.robust_z = measured["robust_z"]
    incident.absolute_delta = measured["absolute_delta"]
    incident.confidence = measured["confidence"]
    incident.dirty = True


def _new_incident(
    instance_id: uuid.UUID,
    rule: _RuleRow,
    detector: str,
    evidence: AnomalyEvidence[RuleAnomalyType],
    state: _State,
    target: AnomalyState,
    now: datetime,
    policy_version: int,
) -> _Incident:
    measured = _measurements(evidence)
    incident_id = uuid.uuid4()
    return _Incident(
        id=incident_id,
        rule_id=rule.id,
        qradar_id=rule.subject.qradar_id,
        is_building_block=rule.subject.is_building_block,
        detector=detector,
        fingerprint=rule_anomaly_fingerprint(instance_id, rule.id, detector),
        severity=evidence.severity.value,
        state=target.value,
        detected_at=now,
        opened_at=now if target is AnomalyState.OPEN else None,
        anomaly_start=evidence.interval_start,
        anomaly_end=None,
        resolved_at=None,
        observed_value=measured["observed_value"],
        expected_value=measured["expected_value"],
        deviation_score=measured["deviation_score"],
        deviation_ratio=measured["deviation_ratio"],
        robust_z=measured["robust_z"],
        absolute_delta=measured["absolute_delta"],
        confidence=measured["confidence"],
        consecutive_buckets=state.consecutive_anomalous,
        policy_version=policy_version,
        explanation=evidence.reason,
        details=_jsonable(evidence.to_dict()),
        dirty=True,
    )


def _transition(
    anomaly_id: uuid.UUID,
    previous: AnomalyState,
    target: AnomalyState,
    start: datetime,
    reason: str,
    reason_i18n: dict[str, Any] | None,
    evidence: AnomalyEvidence[RuleAnomalyType],
    now: datetime,
) -> _Transition:
    return _Transition(
        id=uuid.uuid4(),
        anomaly_id=anomaly_id,
        from_state=previous.value,
        to_state=target.value,
        occurred_at=now,
        bucket_start=start,
        reason=reason,
        reason_i18n=reason_i18n,
        observed=evidence.observed_value,
        expected=evidence.expected_value,
    )


def _open_action(
    rule: _RuleRow,
    detector: str,
    evidence: AnomalyEvidence[RuleAnomalyType],
    start: datetime,
    incident_id: uuid.UUID,
) -> _AlertOpen:
    return _AlertOpen(
        rule_id=rule.id,
        rule_name=rule.name,
        qradar_id=rule.subject.qradar_id,
        is_building_block=rule.subject.is_building_block,
        detector=detector,
        evidence=evidence,
        window_start=start,
        incident_id=incident_id,
    )


def _alert_input(
    instance_id: uuid.UUID,
    instance_name: str,
    action: _AlertOpen,
    width: int,
) -> AlertInput:
    evidence = action.evidence
    context: dict[str, object] = {
        "instance": instance_name,
        "rule_name": action.rule_name,
        "qradar_id": action.qradar_id,
        "detector": action.detector,
        "is_building_block": action.is_building_block,
        "window_start": action.window_start.isoformat(),
        "window_end": (action.window_start + timedelta(seconds=width)).isoformat(),
    }
    if evidence.observed_value is not None:
        context["observed"] = evidence.observed_value
    if evidence.expected_value is not None:
        context["expected"] = evidence.expected_value
    return AlertInput(
        fingerprint=rule_anomaly_fingerprint(instance_id, action.rule_id, action.detector),
        title=_title(action.detector, action.rule_name, action.qradar_id),
        severity=(
            evidence.severity
            if isinstance(evidence.severity, Severity)
            else Severity(str(evidence.severity))
        ),
        source_type=_SOURCE_TYPE,
        source_id=action.rule_id,
        description=evidence.reason,
        evidence=_jsonable(evidence.to_dict()),
        source_anomaly_ids=[str(action.incident_id)],
        context=context,
    )


def _rounds(
    instance_id: uuid.UUID, actions: Sequence[_AlertOpen | _AlertResolve]
) -> list[tuple[list[_AlertOpen], list[_AlertResolve]]]:
    rounds: list[tuple[dict[str, _AlertOpen], dict[str, _AlertResolve]]] = []
    current: tuple[dict[str, _AlertOpen], dict[str, _AlertResolve]] = ({}, {})
    for item in actions:
        fingerprint = rule_anomaly_fingerprint(instance_id, item.rule_id, item.detector)
        opens, resolves = current
        if isinstance(item, _AlertOpen):
            if fingerprint in resolves:
                rounds.append(current)
                current = opens, resolves = {}, {}
            opens[fingerprint] = item
        else:
            resolves[fingerprint] = item
    rounds.append(current)
    return [
        (list(opens.values()), list(resolves.values()))
        for opens, resolves in rounds
        if opens or resolves
    ]
