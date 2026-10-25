"""Phase D IPS behavioural detectors (design D.6, D.8).

Pure functions over an `IpsDetectionContext`, like the WAF detectors: no I/O, no
ORM, every guard unit-testable. Each answers one question about one entity or one
source in one bucket, and none asserts intent: a port sweep is a shape, a high
block ratio describes the IPS's decisions, and a dominant source IP is a traffic
composition.

**What a verdict may rest on.** The scope comes from the detector's declared
per-input requirements (`app.anomaly.capabilities.IPS_DETECTORS`) applied to the
observation; a detector never grants itself lower-bound reasoning. Within that
scope:

  * an input that was not collected, failed, or is unknown yields
    INSUFFICIENT_DATA naming it -- never a zero, never NORMAL;
  * a HEALTHY verdict is returned only where the anomaly's definition is provably
    unmet, and only from an exact count;
  * with INCREASE_ONLY scope every exit that would have been HEALTHY is
    INSUFFICIENT_DATA, because every anomalous guard is non-decreasing in the
    lower-bounded count.

Before any comparison every detector refuses a bucket in maintenance, a
dimension that is unmeasured or under the coverage floor, and a baseline cell
that is missing, unreliable, thin, or built under another profile or normalizer
version.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.anomaly import statistics as rstats
from app.anomaly.capabilities import ips_input_scope
from app.anomaly.evidence import AnomalyEvidence, healthy, insufficient
from app.core.text import percent
from app.ips.fold import GENERIC_NAME_PREFIX
from app.ips.inputs import (
    IPS_ENTITY_EVENTS_METRIC,
    IpsDimensionFacts,
    IpsObservation,
    IpsSourceBucketFacts,
)
from app.models.enums import (
    DetectorSignal,
    IpsAnomalyType,
    IpsDimension,
    RobustScoreStatus,
    Severity,
    WafMeasurement,
)
from app.waf.detector import WafBaselineCell, _expected_band
from app.waf.inputs import HistoryFacts
from app.waf.measurement import ScopeDecision, WafVerdictScope

#: Version of the structured evidence contract in `extra`. Bump on a breaking
#: change to its keys.
EVIDENCE_VERSION = 1
CONTRIBUTOR_EVIDENCE_LIMIT = 10


@dataclass(frozen=True)
class IpsThresholds:
    """Phase D guards. Separate from the WAF ones so neither can move the other."""

    deviation_z: float = 3.5
    mad_floor_ratio: float = 0.1
    min_coverage: float = 0.9
    baseline_min_samples: int = 8
    #: Entity rate spikes.
    spike_ratio: float = 2.0
    min_absolute_delta_events: float = 50.0
    min_bucket_events: float = 20.0
    #: Port fan-out (distinct destination ports of one source IP).
    fanout_min_ports: float = 20.0
    fanout_ratio: float = 2.0
    fanout_min_delta_ports: float = 15.0
    #: Block ratio.
    ratio_min_denominator: int = 50
    ratio_min_numerator: int = 10
    ratio_min_increase: float = 0.10
    ratio_min_multiple: float = 2.0
    #: Source concentration.
    concentration_min_source_events: int = 50
    concentration_min_top1_events: int = 20
    concentration_min_increase: float = 0.20
    #: New destination port.
    new_port_min_events: int = 20
    new_port_min_buckets: int = 2
    new_port_window_buckets: int = 12
    new_port_history_days: int = 7
    admission_min_events: int = 3
    open_after: int = 2
    resolve_after: int = 3


@dataclass
class IpsBaselineCell(WafBaselineCell):
    """A WAF-shaped seasonal cell that also states what it was built under.

    A missing version is incompatible, not compatible: a detector refuses a cell
    that cannot say which profile and normalizer it describes.
    """

    profile_version: int | None = None
    normalizer_version: int | None = None


@dataclass(frozen=True)
class IpsNoveltyFacts:
    """What the engine established about one port entity's first appearance."""

    first_seen_at: datetime
    #: End of the novelty window: the port is "new" before this instant.
    window_end: datetime
    within_window: bool
    #: Retired, then seen again: the port is not new.
    readmitted: bool
    history: HistoryFacts
    history_start: datetime
    history_end: datetime
    history_buckets: int
    #: Set when the first-appearance bucket's port discovery or admission was incomplete.
    appearance_flood: str | None
    events_since_first_seen: int
    buckets_with_traffic: int
    #: Some bucket since first appearance counted only a lower bound.
    lower_bound: bool


@dataclass
class IpsDetectionContext:
    observation: IpsObservation
    baseline: IpsBaselineCell | None = None
    thresholds: IpsThresholds = field(default_factory=IpsThresholds)
    novelty: IpsNoveltyFacts | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class IpsVerdict:
    detector_type: IpsAnomalyType
    evidence: AnomalyEvidence[IpsAnomalyType]

    @property
    def signal(self) -> DetectorSignal:
        return self.evidence.signal

    @property
    def is_anomalous(self) -> bool:
        return self.evidence.is_anomalous


# --------------------------------------------------------------------- helpers
def _pct(value: float | None) -> str:
    """The share spelling for IPS sentences: one decimal of the shared helper."""
    return percent(value, precision=1)


def _events(value: float) -> str:
    return f"{value:,.0f}"


def _is_unusable_port_key(value_key: str) -> bool:
    """Port 0 and protocol 255 are absent values (T-027), never services."""
    port, _, protocol = value_key.partition("/")
    return not port.isdigit() or int(port) == 0 or protocol == "255"


def _confidence(cell: WafBaselineCell, magnitude: float, thr: IpsThresholds) -> float:
    reliability = min(1.0, cell.sample_count / 30.0)
    strength = min(1.0, abs(magnitude) / (2 * thr.deviation_z))
    return round((0.4 * reliability + 0.6 * strength) * min(1.0, cell.completeness + 0.5), 3)


def _score(
    observed: float,
    cell: WafBaselineCell,
    thr: IpsThresholds,
    facts: dict[str, Any],
    fmt: Callable[[float], str],
) -> tuple[bool, str, float]:
    """The seasonal score guard: (passed, basis or reason, magnitude)."""
    z, status = rstats.robust_z_with_status(
        observed, cell.median, cell.mad, mad_floor_ratio=thr.mad_floor_ratio
    )
    facts["robust_score_status"] = status.value
    facts["robust_z"] = round(z, 3)
    if status is RobustScoreStatus.OK:
        if z < thr.deviation_z:
            return False, f"robust z={z:.2f} has not reached +{thr.deviation_z}", z
        return True, f"robust z={z:.2f} (threshold +{thr.deviation_z})", z
    # MAD is zero: the score is an artefact of the floored scale. Fall back to the
    # deterministic band; never return healthy merely because the baseline is steady.
    _, upper = _expected_band(cell, thr.mad_floor_ratio)
    facts["fallback_bound"] = round(upper, 6)
    if observed <= upper:
        return (
            False,
            f"robust score unavailable (MAD=0) and {fmt(observed)} is within the "
            f"historical p95 {fmt(upper)}",
            thr.deviation_z,
        )
    return (
        True,
        f"robust score unavailable (MAD=0); {fmt(observed)} is above the historical "
        f"p95 {fmt(upper)}",
        thr.deviation_z,
    )


def _cell_facts(cell: WafBaselineCell, expected: float) -> dict[str, Any]:
    return {
        "expected": round(expected, 6),
        "numerator_sum": cell.numerator_sum,
        "denominator_sum": cell.denominator_sum,
        "median": round(cell.median, 6),
        "p05": cell.p05,
        "p95": round(cell.p95, 6) if cell.p95 is not None else None,
        "samples": cell.sample_count,
        "completeness": round(cell.completeness, 3),
        "baseline_version": cell.baseline_version,
        "metric_identity": cell.metric_semantics,
    }


def _contributors(
    bucket: IpsSourceBucketFacts, key: str, value_name: str, *, total: int | None
) -> dict[str, Any]:
    """The bucket's bounded contributor list, as stored. Never extended or re-ranked."""
    dimension = bucket.dimension(IpsDimension(key))
    measurement = dimension.waf_measurement if dimension is not None else None
    exact = measurement is WafMeasurement.EXACT
    raw = bucket.contributors.get(key) if bucket.contributors else None
    items: list[dict[str, Any]] = []
    for item in list(raw or [])[:CONTRIBUTOR_EVIDENCE_LIMIT]:
        entry = {"source_ip": item.get("source_ip"), value_name: item.get(value_name)}
        if exact and total and isinstance(entry[value_name], int):
            entry["share"] = round(entry[value_name] / total, 4)
        items.append(entry)
    evidence = {
        "dimension": key,
        "status": (
            "AVAILABLE"
            if exact
            else "TRUNCATED"
            if measurement is WafMeasurement.LOWER_BOUND
            else "UNAVAILABLE"
        ),
        "scope": "source IP as QRadar normalized it; the list is bounded and not exhaustive",
        "items": items,
    }
    if measurement is WafMeasurement.LOWER_BOUND:
        evidence["note"] = "the contributor search was a lower bound; shares are withheld"
    elif measurement is None:
        evidence["note"] = "the contributor search was unavailable; shares are withheld"
    return evidence


class _Guard:
    """Shared exits for one verdict, honouring the declared scope."""

    def __init__(
        self,
        ctx: IpsDetectionContext,
        dt: IpsAnomalyType,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> None:
        self.ctx, self.dt = ctx, dt
        self.obs = ctx.observation
        self.bucket = ctx.observation.bucket
        self.thr = ctx.thresholds
        self.start = start or self.bucket.bucket_start
        self.end = end or self.bucket.bucket_end
        history = ctx.novelty.history if ctx.novelty is not None else None
        self.decision: ScopeDecision = ips_input_scope(dt, self.obs, history)
        self.lower_bound = self.decision.scope is WafVerdictScope.INCREASE_ONLY

    def verdict(self, ev: AnomalyEvidence[IpsAnomalyType]) -> IpsVerdict:
        return IpsVerdict(detector_type=self.dt, evidence=ev)

    def insufficient(self, reason: str, sample_count: int = 0) -> IpsVerdict:
        return self.verdict(insufficient(
            self.dt, interval_start=self.bucket.bucket_start,
            interval_end=self.bucket.bucket_end, reason=reason, sample_count=sample_count,
        ))

    def healthy(
        self, reason: str, *, observed: float | None, expected: float | None
    ) -> IpsVerdict:
        return self.verdict(healthy(
            self.dt, interval_start=self.bucket.bucket_start,
            interval_end=self.bucket.bucket_end, observed=observed,
            expected=round(expected, 6) if expected is not None else None, reason=reason,
        ))

    def not_proven(
        self, reason: str, *, observed: float, expected: float | None
    ) -> IpsVerdict:
        """The normal exit of a guard: healthy for an exact input, no verdict for a bound."""
        if self.lower_bound:
            return self.insufficient(
                f"{reason}; the count is a lower bound ({self.decision.reason}), so a normal "
                "verdict cannot be concluded from it"
            )
        return self.healthy(reason, observed=round(observed, 6), expected=expected)

    # ---- preconditions common to every detector -------------------------------
    def maintenance(self) -> IpsVerdict | None:
        if self.bucket.in_maintenance:
            return self.insufficient("the source was in maintenance during this bucket")
        return None

    def dimension(
        self, dimension: IpsDimension, label: str, *, coverage: bool = True
    ) -> IpsVerdict | None:
        facts: IpsDimensionFacts | None = self.bucket.dimension(dimension)
        if facts is None or not facts.measured:
            state = facts.state if facts is not None else "not collected"
            return self.insufficient(
                f"the {label} search was {state} for this bucket: the value is unavailable, "
                "not zero"
            )
        if coverage and not facts.coverage_met(self.thr.min_coverage):
            ratio = facts.coverage_ratio
            seen = "was not measurable" if ratio is None else f"was {percent(ratio)}"
            return self.insufficient(
                f"{label} coverage {seen}, below the {percent(self.thr.min_coverage)} floor: the "
                "events without a value may differ in kind from the ones with one, so the "
                "visible numbers bound nothing"
            )
        return None

    def scope(self) -> IpsVerdict | None:
        if self.decision.scope is WafVerdictScope.NONE:
            return self.insufficient(self.decision.reason or "the inputs do not support a verdict")
        return None

    def cell(self, metric: str, *, pooled: bool = False) -> IpsBaselineCell | IpsVerdict:
        cell = self.ctx.baseline
        if cell is None:
            return self.insufficient(f"no {metric} baseline for this weekday/hour cell yet")
        if (cell.profile_version, cell.normalizer_version) != (
            self.bucket.profile_version, self.bucket.normalizer_version
        ):
            return self.insufficient(
                f"the {metric} baseline was built under profile version {cell.profile_version} "
                f"and normalizer version {cell.normalizer_version}, not the bucket's "
                f"{self.bucket.profile_version} and {self.bucket.normalizer_version}"
            )
        if not cell.is_reliable or cell.sample_count < self.thr.baseline_min_samples:
            return self.insufficient(
                f"the {metric} baseline has {cell.sample_count} sample(s), below the minimum "
                f"of {self.thr.baseline_min_samples} for a defensible verdict",
                sample_count=cell.sample_count,
            )
        if pooled and not cell.denominator_sum:
            return self.insufficient(f"the {metric} baseline has no pooled denominator")
        return cell


def _anomalous(
    g: _Guard, *, cell: WafBaselineCell, observed: float, expected: float, z: float | None,
    magnitude: float, severity: Severity, threshold: float, reason: str, facts: dict[str, Any],
    start: datetime | None = None, end: datetime | None = None,
) -> IpsVerdict:
    return g.verdict(AnomalyEvidence(
        anomaly_type=g.dt,
        signal=DetectorSignal.ANOMALOUS,
        severity=severity,
        observed_value=round(observed, 6),
        expected_value=round(expected, 6),
        baseline_low=cell.p05,
        baseline_high=cell.p95,
        deviation_score=z,
        threshold=threshold,
        sample_count=cell.sample_count,
        interval_start=start or g.bucket.bucket_start,
        interval_end=end or g.bucket.bucket_end,
        confidence=_confidence(cell, magnitude, g.thr),
        reason=reason,
        extra=facts,
    ))


# ---------------------------------------------------------------- entity spikes
@dataclass(frozen=True)
class _EntitySpike:
    detector_type: IpsAnomalyType
    dimension: IpsDimension
    label: str
    caveat: str


PORT_SPIKE = _EntitySpike(
    IpsAnomalyType.IPS_PORT_RATE_SPIKE, IpsDimension.DESTINATION_PORT, "destination port",
    "More events on a port describe traffic volume, not intent: a scan, a deployment and a "
    "misconfigured client look alike here.",
)
DESTINATION_SPIKE = _EntitySpike(
    IpsAnomalyType.IPS_DESTINATION_RATE_SPIKE, IpsDimension.DESTINATION_IP, "destination IP",
    "More events towards a destination describe traffic volume, not an attack on it.",
)
SIGNATURE_SPIKE = _EntitySpike(
    IpsAnomalyType.IPS_SIGNATURE_BURST, IpsDimension.SIGNATURE, "signature",
    "A burst of one signature describes how often the IPS matched it, not that the "
    "matched activity succeeded.",
)


def detect_entity_spike(ctx: IpsDetectionContext, d: _EntitySpike) -> IpsVerdict:
    """Entity events above the entity's seasonal band (WAF spike math, IPS thresholds).

    Conjunctive: minimum events, ratio, absolute delta, and the robust score (or,
    at MAD=0, the p95 band) must all clear.
    """
    g = _Guard(ctx, d.detector_type)
    obs, thr = g.obs, g.thr
    if (early := g.maintenance()) is not None:
        return early
    entity = obs.entity
    if entity is None or obs.count is None:
        return g.insufficient(f"no {d.label} entity was supplied for this verdict")
    if entity.dimension is not d.dimension:
        return g.insufficient(
            f"a {entity.dimension.value} entity is not judged by {d.detector_type.value}"
        )
    if d.dimension is IpsDimension.DESTINATION_PORT and _is_unusable_port_key(entity.value_key):
        return g.insufficient(
            "port 0 and protocol 255 are absent values, not a service: no claim about them"
        )
    if (
        d.dimension is IpsDimension.SIGNATURE
        and (entity.display_name or "").startswith(GENERIC_NAME_PREFIX)
    ):
        return g.insufficient(
            "the signature's name is generic: a claim about a specific signature is not safe"
        )
    if (early := g.dimension(d.dimension, d.label)) is not None:
        return early
    if obs.count.events is None:
        return g.insufficient(obs.count.reason or "the entity's count is unknown for this bucket")
    if (early := g.scope()) is not None:
        return early
    cell_or_exit = g.cell(IPS_ENTITY_EVENTS_METRIC)
    if isinstance(cell_or_exit, IpsVerdict):
        return cell_or_exit
    cell = cell_or_exit

    observed = float(obs.count.events)
    expected = cell.median
    delta = observed - expected
    subject = f"{d.label} {entity.display_name or entity.value_key}"
    facts: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "detector_type": d.detector_type.value,
        "entity": {
            "dimension": entity.dimension.value,
            "value_key": entity.value_key,
            "display_name": entity.display_name,
        },
        "observed": {
            "metric": IPS_ENTITY_EVENTS_METRIC,
            "events": obs.count.events,
            "measurement": (obs.count.measurement or WafMeasurement.EXACT).value,
        },
        "expected": _cell_facts(cell, expected),
        "absolute_delta": round(delta, 3),
        "window": {"start": g.start.isoformat(), "end": g.end.isoformat()},
        "provenance": {
            "record": "ips_source_bucket", "bucket_start": g.bucket.bucket_start.isoformat(),
        },
        "observed_measurement": (obs.count.measurement or WafMeasurement.EXACT).value,
        "caveat": d.caveat,
        **ctx.extra,
    }
    return _spike(
        g, cell=cell, observed=observed, subject=subject, unit="events",
        min_value=thr.min_bucket_events, ratio_min=thr.spike_ratio,
        min_delta=thr.min_absolute_delta_events, facts=facts,
    )


def _spike(
    g: _Guard, *, cell: IpsBaselineCell, observed: float, subject: str, unit: str,
    min_value: float, ratio_min: float, min_delta: float, facts: dict[str, Any],
    observed_lower_bound: bool | None = None,
) -> IpsVerdict:
    thr = g.thr
    expected = cell.median
    delta = observed - expected
    if observed < min_value:
        return g.not_proven(
            f"{_events(observed)} {unit}/bucket is below the {_events(min_value)} minimum for a "
            "defensible verdict", observed=observed, expected=expected,
        )
    ratio = rstats.ratio(observed, expected)
    facts["ratio"] = round(ratio, 4) if ratio is not None else None
    if ratio is None:
        # Expected is zero. A spike from nothing is real but has no ratio, so the
        # absolute-delta guard carries it alone.
        facts["ratio_basis"] = "expected_zero"
    elif ratio < ratio_min:
        return g.not_proven(
            f"observed/expected ratio {ratio:.2f} has not reached {ratio_min:.2f}",
            observed=observed, expected=expected,
        )
    if delta < min_delta:
        return g.not_proven(
            f"increase of {_events(delta)} {unit}/bucket is below the {_events(min_delta)} "
            "minimum absolute delta", observed=observed, expected=expected,
        )
    passed, basis, magnitude = _score(observed, cell, thr, facts, _events)
    if not passed:
        return g.not_proven(basis, observed=observed, expected=expected)
    severity = (
        Severity.HIGH
        if (ratio is not None and ratio / ratio_min >= 2.0) or magnitude >= thr.deviation_z * 1.5
        else Severity.MEDIUM
    )
    ratio_text = f"{ratio:.2f}x expected" if ratio is not None else "expected none"
    count_is_lower_bound = g.lower_bound if observed_lower_bound is None else observed_lower_bound
    bound = f"at least {_events(observed)}" if count_is_lower_bound else _events(observed)
    why = f" (a lower bound: {g.decision.reason})" if count_is_lower_bound else ""
    return _anomalous(
        g, cell=cell, observed=observed, expected=expected, z=facts.get("robust_z"),
        magnitude=magnitude, severity=severity, threshold=thr.deviation_z, facts=facts,
        reason=(
            f"{bound} {unit}{why} for {subject} is above the baseline median "
            f"{expected:.1f} ({ratio_text}, +{_events(delta)} {unit}/bucket); {basis}"
        ),
    )


def detect_port_rate_spike(ctx: IpsDetectionContext) -> IpsVerdict:
    return detect_entity_spike(ctx, PORT_SPIKE)


def detect_destination_rate_spike(ctx: IpsDetectionContext) -> IpsVerdict:
    return detect_entity_spike(ctx, DESTINATION_SPIKE)


def detect_signature_burst(ctx: IpsDetectionContext) -> IpsVerdict:
    return detect_entity_spike(ctx, SIGNATURE_SPIKE)


