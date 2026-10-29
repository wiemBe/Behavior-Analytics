"""Phase B.2 WAF behavioural detectors: ratios, composition, novelty.

Pure functions over `WafDetectionContext`, like `detect_request_rate_spike`: no
I/O, no ORM, every guard unit-testable. Each answers one question about one
endpoint in one bucket -- *what changed, compared with what, and which source IPs
contributed* -- and none asserts intent. A high block ratio is not a successful
attack, a 5xx rise is not malicious traffic, and a dominant source IP is a
traffic composition, not an attacker.

**What a verdict may rest on.** The engine computes each detector's verdict
scope from its declared per-input requirements (`app.anomaly.capabilities`) and
passes it in; a detector never grants itself lower-bound reasoning. Within that
scope, guards are ordered so that:

  * an input that was not collected, or whose search failed, yields
    INSUFFICIENT_DATA naming the breakdown -- never a zero, never NORMAL;
  * a HEALTHY verdict is returned only where the anomaly's definition is
    provably unmet: with an exact numerator below its minimum, no proportion can
    be material, whatever the denominator;
  * with INCREASE_ONLY scope (a lower-bound numerator over an exact
    denominator) every exit that would have been HEALTHY is INSUFFICIENT_DATA,
    because every anomalous guard is non-decreasing in the numerator.

**Ratios compare aggregate proportions.** The expected proportion is the
cell's pooled numerator over its pooled denominator -- a bucket with two
requests weighs two requests -- and the observed proportion must clear, all at
once: a minimum denominator, a minimum numerator, a minimum rise in proportion
units, a minimum multiple of the expected proportion (when it is nonzero), and
the seasonal robust score (or, when the cell's MAD is zero, its p95 band).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.anomaly import statistics as rstats
from app.anomaly.evidence import AnomalyEvidence, healthy, insufficient
from app.i18n.messages import MessageRef, render
from app.models.enums import (
    DetectorSignal,
    RobustScoreStatus,
    Severity,
    WafAnomalyType,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.waf.baseline import (
    BLOCK_RATIO_METRIC,
    CLIENT_ERROR_RATIO_METRIC,
    SERVER_ERROR_RATIO_METRIC,
    TOP_CLIENT_SHARE_METRIC,
    WAF_BASELINE_METRIC,
)
from app.waf.detector import (
    Reason,
    WafBaselineCell,
    WafDetectionContext,
    WafSpikeVerdict,
    _paired,
    scope_reason_ref,
    split_reason,
)
from app.waf.inputs import HistoryFacts
from app.waf.measurement import WafVerdictScope
from app.waf.observations import BreakdownCounts, BreakdownStatus, EndpointObservation

#: Version of the structured evidence contract in `extra`. Bump on a breaking
#: change to its keys.
EVIDENCE_VERSION = 1


@dataclass(frozen=True)
class WafBehaviorThresholds:
    deviation_z: float = 3.5
    min_coverage: float = 0.9
    mad_floor_ratio: float = 0.1
    ratio_min_denominator: int = 50
    ratio_min_numerator: int = 10
    ratio_min_increase: float = 0.10
    ratio_min_multiple: float = 2.0
    concentration_min_endpoint_requests: int = 50
    concentration_min_client_requests: int = 20
    concentration_min_increase: float = 0.20
    new_endpoint_min_requests: int = 20
    new_endpoint_min_buckets: int = 2
    new_endpoint_window_buckets: int = 12
    new_endpoint_history_days: int = 7
    admission_min_requests: int = 3


@dataclass(frozen=True)
class NoveltyFacts:
    """What the engine established about one endpoint's first appearance."""

    first_seen_at: datetime
    #: End of the novelty window: the endpoint is "new" before this instant.
    window_end: datetime
    within_window: bool
    history: HistoryFacts
    history_start: datetime
    history_end: datetime
    history_buckets: int
    #: Set when the first-appearance bucket had truncated discovery or admission: a reference
    #: from the engine, or the English text of a caller that has no code.
    appearance_flood: Reason | None
    requests_since_first_seen: int
    buckets_with_traffic: int
    #: Some bucket since first appearance counted only a lower bound.
    lower_bound: bool


# --------------------------------------------------------------------- helpers
def _result(dt: WafAnomalyType, ev: AnomalyEvidence[WafAnomalyType]) -> WafSpikeVerdict:
    return WafSpikeVerdict(detector_type=dt, evidence=ev)


def _ref(name: str, **params: object) -> MessageRef:
    return MessageRef(f"waf.{name}", params)


def _entity(ctx: WafDetectionContext) -> dict[str, Any] | None:
    return ctx.extra.get("entity")


def _subject(ctx: WafDetectionContext) -> MessageRef:
    entity = _entity(ctx) or {}
    method, endpoint = entity.get("http_method"), entity.get("normalized_endpoint")
    if method and endpoint:
        return _ref("subject.named", method=str(method), endpoint=str(endpoint))
    return _ref("subject.this_endpoint")


def contributors(obs: EndpointObservation, *, min_top_requests: int) -> dict[str, Any]:
    """Top source-IP contributors to the endpoint's traffic in the bucket.

    Scoped to all of the endpoint's requests in the bucket: the breakdowns are
    separate searches, so a blocked-request or 5xx verdict cannot attribute its
    numerator to a source IP, and the evidence says so rather than implying it.
    Shares are given only when the breakdown is exact; a truncated list is
    marked TRUNCATED and is neither exhaustive nor exactly ranked.
    """
    status, counts = obs.breakdowns.client, obs.counts
    scope = "all requests to the endpoint in this bucket; source IP as QRadar normalized it"
    if status not in (WafTelemetryStatus.EXACT, WafTelemetryStatus.LOWER_BOUND):
        return {
            "dimension": "SOURCE_IP",
            "status": "UNAVAILABLE",
            "reason": f"the source-IP breakdown was {status.value}",
            "items": [],
        }
    exact = status is WafTelemetryStatus.EXACT
    items = [dict(i) for i in counts.top_clients or ()]
    total = counts.client_events or 0
    for item in items:
        requests = int(item.get("requests", 0))
        item["share"] = round(requests / total, 4) if exact and total > 0 else None
    out: dict[str, Any] = {
        "dimension": "SOURCE_IP",
        "status": "AVAILABLE" if exact else "TRUNCATED",
        "scope": scope,
        "items": items,
        "distinct_sources": counts.client_sources if exact else None,
        "exhaustive": bool(exact and counts.client_sources is not None
                           and counts.client_sources <= len(items)),
    }
    if not items:
        out["note"] = (
            f"no source IP reached {min_top_requests} requests, so none was recorded"
        )
    if not exact:
        out["note"] = (
            "the breakdown was truncated: contributors are among visible groups only, "
            "counts are lower bounds and shares are withheld"
        )
    return out


def _cell_facts(cell: WafBaselineCell, expected: float) -> dict[str, Any]:
    return {
        "proportion": round(expected, 6),
        "numerator_sum": cell.numerator_sum,
        "denominator_sum": cell.denominator_sum,
        "median_bucket_proportion": round(cell.median, 6),
        "p95_bucket_proportion": round(cell.p95, 6) if cell.p95 is not None else None,
        "material_buckets": cell.sample_count,
        "completeness": round(cell.completeness, 3),
        "baseline_version": cell.baseline_version,
        "metric_identity": cell.metric_semantics,
    }


def _caveat_message(message: MessageRef | None) -> dict[str, Any]:
    """The `caveat_code` / `caveat_params` that sit beside `caveat` in the evidence."""
    if message is None:
        return {}
    wire = message.to_wire()
    return {"caveat_code": wire["code"], "caveat_params": wire["params"]}


def _volume_context(ctx: WafDetectionContext, obs: EndpointObservation) -> dict[str, Any]:
    cell = ctx.baselines.get(WAF_BASELINE_METRIC)
    expected = cell.median if cell is not None and cell.is_reliable else None
    return {
        "observed_requests": obs.request_count,
        "request_count_measurement": obs.measurement.value if obs.measurement else None,
        "expected_requests": round(expected, 1) if expected is not None else None,
        "ratio": (
            round(rstats.ratio(float(obs.request_count), expected) or 0.0, 3)
            if expected
            else None
        ),
    }


def _surprise(
    observed: float, cell: WafBaselineCell, thr: WafBehaviorThresholds, facts: dict[str, Any]
) -> tuple[bool, MessageRef, float]:
    """The seasonal score guard: (passed, basis or reason, magnitude)."""
    z, status = rstats.robust_z_with_status(
        observed, cell.median, cell.mad, mad_floor_ratio=thr.mad_floor_ratio
    )
    facts["robust_score_status"] = status.value
    facts["robust_z"] = round(z, 3)
    if status is RobustScoreStatus.OK:
        if z < thr.deviation_z:
            return (
                False, _ref("basis.z_not_reached", score=z, threshold=thr.deviation_z), z
            )
        return True, _ref("basis.z", score=z, threshold=thr.deviation_z), z
    upper = cell.p95 if cell.p95 is not None else cell.median
    facts["fallback_bound"] = round(upper, 6)
    if observed <= upper:
        return (
            False,
            _ref("basis.mad_within_p95", observed=observed, bound=upper),
            thr.deviation_z,
        )
    return (
        True,
        _ref("basis.mad_above_p95", observed=observed, bound=upper),
        thr.deviation_z,
    )


class _Guard:
    """Shared exits for one verdict, honouring the scope."""

    def __init__(self, ctx: WafDetectionContext, dt: WafAnomalyType) -> None:
        self.ctx, self.dt = ctx, dt
        self.obs: EndpointObservation = ctx.observation
        self.lower_bound = ctx.effective_scope is WafVerdictScope.INCREASE_ONLY

    def insufficient(self, reason: Reason, sample_count: int = 0) -> WafSpikeVerdict:
        text, message = split_reason(reason)
        return _result(self.dt, insufficient(
            self.dt, interval_start=self.obs.bucket_start, interval_end=self.obs.bucket_end,
            reason=text, sample_count=sample_count, reason_message=message,
        ))

    def not_proven(
        self, reason: MessageRef, *, observed: float, expected: float | None
    ) -> WafSpikeVerdict:
        if self.lower_bound:
            return self.insufficient(
                _ref(
                    "guard.not_proven_lower_bound",
                    reason=reason,
                    scope=(
                        scope_reason_ref(self.ctx.scope_reason)
                        or _ref("scope.unclassified")
                    ),
                )
            )
        text, message = split_reason(reason)
        return _result(self.dt, healthy(
            self.dt, interval_start=self.obs.bucket_start, interval_end=self.obs.bucket_end,
            observed=round(observed, 6),
            expected=round(expected, 6) if expected is not None else None,
            reason=text, reason_message=message,
        ))

    def baseline(self, metric: str) -> WafBaselineCell | WafSpikeVerdict:
        if self.ctx.baseline_incompatibility is not None:
            return self.insufficient(
                _paired(
                    self.ctx.baseline_incompatibility, self.ctx.baseline_incompatibility_message
                )
            )
        cell = self.ctx.baseline
        if cell is None:
            return self.insufficient(_ref("guard.no_baseline", metric=metric))
        if not cell.is_reliable:
            return self.insufficient(
                _ref("guard.baseline_samples", metric=metric, samples=cell.sample_count),
                sample_count=cell.sample_count,
            )
        if not cell.denominator_sum:
            return self.insufficient(_ref("guard.baseline_no_denominator", metric=metric))
        return cell


# ---------------------------------------------------------------------- ratios
@dataclass(frozen=True)
class RatioDefinition:
    detector_type: WafAnomalyType
    metric: str
    breakdown: str
    status: Callable[[BreakdownStatus], WafTelemetryStatus]
    semantics: Callable[[BreakdownStatus], str | None]
    numerator: Callable[[BreakdownCounts], int | None]
    denominator: Callable[[BreakdownCounts], int | None]
    uncertain: Callable[[BreakdownCounts], int | None]
    numerator_noun: str
    denominator_noun: str
    uncertain_noun: str
    #: Neutral statement of what the finding is not.
    caveat: str
    #: The registry references behind the noun phrases and the caveat, when the definition has
    #: them (the sentence takes the reference; the text stays the stored English).
    numerator_ref: MessageRef | None = None
    denominator_ref: MessageRef | None = None
    caveat_ref: MessageRef | None = None

    @property
    def numerator_phrase(self) -> Reason:
        return self.numerator_ref or self.numerator_noun

    @property
    def denominator_phrase(self) -> Reason:
        return self.denominator_ref or self.denominator_noun


def _fixed_ref(code: str) -> tuple[str, MessageRef]:
    """A fixed phrase of the registry: its English and its reference."""
    ref = MessageRef(code)
    return render(ref), ref


_BLOCKED, _BLOCKED_REF = _fixed_ref("waf.noun.blocked_requests")
_WAF_ACTION, _WAF_ACTION_REF = _fixed_ref("waf.noun.waf_action_requests")
_HTTP_5XX, _HTTP_5XX_REF = _fixed_ref("waf.noun.http_5xx")
_HTTP_4XX, _HTTP_4XX_REF = _fixed_ref("waf.noun.http_4xx")
_WITH_CODE, _WITH_CODE_REF = _fixed_ref("waf.noun.response_code_requests")
_CAVEAT_BLOCK, _CAVEAT_BLOCK_REF = _fixed_ref("waf.caveat.block_ratio")
_CAVEAT_5XX, _CAVEAT_5XX_REF = _fixed_ref("waf.caveat.server_error_ratio")
_CAVEAT_4XX, _CAVEAT_4XX_REF = _fixed_ref("waf.caveat.client_error_ratio")
_CAVEAT_CONCENTRATION, _CAVEAT_CONCENTRATION_REF = _fixed_ref("waf.caveat.client_concentration")
_CAVEAT_NEW_ENDPOINT, _CAVEAT_NEW_ENDPOINT_REF = _fixed_ref("waf.caveat.new_endpoint")

BLOCK_RATIO = RatioDefinition(
    detector_type=WafAnomalyType.WAF_BLOCK_RATIO_SPIKE,
    metric=BLOCK_RATIO_METRIC,
    breakdown="ACTION",
    status=lambda b: b.action,
    semantics=lambda b: b.action_semantics,
    numerator=lambda c: c.blocked,
    denominator=lambda c: c.action_events,
    uncertain=lambda c: c.unmapped_actions,
    numerator_noun=_BLOCKED,
    denominator_noun=_WAF_ACTION,
    uncertain_noun="requests with an action value the profile's map does not name",
    caveat=_CAVEAT_BLOCK,
    numerator_ref=_BLOCKED_REF,
    denominator_ref=_WAF_ACTION_REF,
    caveat_ref=_CAVEAT_BLOCK_REF,
)
SERVER_ERROR_RATIO = RatioDefinition(
    detector_type=WafAnomalyType.WAF_ERROR_RATIO_SPIKE,
    metric=SERVER_ERROR_RATIO_METRIC,
    breakdown="RESPONSE_CODE",
    status=lambda b: b.response_code,
    semantics=lambda b: b.response_code_semantics,
    numerator=lambda c: c.server_errors,
    denominator=lambda c: c.response_code_events,
    uncertain=lambda c: c.unclassified_responses,
    numerator_noun=_HTTP_5XX,
    denominator_noun=_WITH_CODE,
    uncertain_noun="response-code values that are not a three-digit status",
    caveat=_CAVEAT_5XX,
    numerator_ref=_HTTP_5XX_REF,
    denominator_ref=_WITH_CODE_REF,
    caveat_ref=_CAVEAT_5XX_REF,
)
CLIENT_ERROR_RATIO = RatioDefinition(
    detector_type=WafAnomalyType.WAF_CLIENT_ERROR_RATIO_SPIKE,
    metric=CLIENT_ERROR_RATIO_METRIC,
    breakdown="RESPONSE_CODE",
    status=lambda b: b.response_code,
    semantics=lambda b: b.response_code_semantics,
    numerator=lambda c: c.client_errors,
    denominator=lambda c: c.response_code_events,
    uncertain=lambda c: c.unclassified_responses,
    numerator_noun=_HTTP_4XX,
    denominator_noun=_WITH_CODE,
    uncertain_noun="response-code values that are not a three-digit status",
    caveat=_CAVEAT_4XX,
    numerator_ref=_HTTP_4XX_REF,
    denominator_ref=_WITH_CODE_REF,
    caveat_ref=_CAVEAT_4XX_REF,
)


def _measured(status: WafTelemetryStatus) -> bool:
    return status in (WafTelemetryStatus.EXACT, WafTelemetryStatus.LOWER_BOUND)


def _unavailable(breakdown: str, status: WafTelemetryStatus) -> MessageRef:
    name = (
        "guard.breakdown_not_collected"
        if status is WafTelemetryStatus.NOT_COLLECTED
        else "guard.breakdown_failed"
    )
    return _ref(name, breakdown=breakdown)


def _low_source_coverage(ratio: float, floor: float) -> MessageRef:
    return _ref("guard.source_coverage_low", covered=ratio, floor=floor)


def detect_ratio(ctx: WafDetectionContext, d: RatioDefinition) -> WafSpikeVerdict:
    g = _Guard(ctx, d.detector_type)
    obs, thr = g.obs, ctx.behavior
    assert obs is not None and thr is not None, "the engine supplies observation and thresholds"
    status = d.status(obs.breakdowns)
    if not _measured(status):
        return g.insufficient(_unavailable(d.breakdown, status))
    if ctx.effective_scope is WafVerdictScope.NONE:
        return g.insufficient(
            scope_reason_ref(ctx.scope_reason) or _ref("scope.inputs_unsupported")
        )
    k = d.numerator(obs.counts) or 0
    n = d.denominator(obs.counts) or 0
    uncertain = d.uncertain(obs.counts) or 0

    if obs.endpoint_coverage_ratio < thr.min_coverage:
        return g.insufficient(_low_source_coverage(obs.endpoint_coverage_ratio, thr.min_coverage))

    # The population the ratio is over must be (nearly) all of the endpoint's
    # requests. The request count is exact here: the scope requires it.
    if obs.request_count > 0 and min(1.0, n / obs.request_count) < thr.min_coverage:
        return g.insufficient(
            _ref(
                "ratio.field_coverage_low",
                covered=min(1.0, n / obs.request_count),
                field=d.breakdown.lower(),
                floor=thr.min_coverage,
            )
        )
    cell_or_exit = g.baseline(d.metric)
    if isinstance(cell_or_exit, WafSpikeVerdict):
        return cell_or_exit
    cell = cell_or_exit
    expected = (cell.numerator_sum or 0.0) / (cell.denominator_sum or 1.0)

    if k < thr.ratio_min_numerator:
        return g.not_proven(
            _ref(
                "ratio.numerator_below_min",
                count=k, noun=d.numerator_phrase, minimum=thr.ratio_min_numerator,
            ),
            observed=k / n if n else 0.0, expected=expected,
        )
    if n < thr.ratio_min_denominator:
        return g.insufficient(
            _ref(
                "ratio.denominator_below_min",
                count=n, noun=d.denominator_phrase, minimum=thr.ratio_min_denominator,
            )
        )
    observed = k / n
    rise = observed - expected
    multiple = rstats.ratio(observed, expected)
    measurement = WafMeasurement.LOWER_BOUND if g.lower_bound else WafMeasurement.EXACT
    facts: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "detector_type": d.detector_type.value,
        "entity": _entity(ctx),
        "observed": {
            "metric": d.metric,
            "proportion": round(observed, 6),
            "numerator": k,
            "numerator_label": d.numerator_noun,
            "denominator": n,
            "denominator_label": d.denominator_noun,
            "uncertain": uncertain,
            "uncertain_label": d.uncertain_noun,
            "measurement": {
                "numerator": measurement.value,
                "denominator": WafMeasurement.EXACT.value,
            },
        },
        "expected": _cell_facts(cell, expected),
        "absolute_deviation": round(rise, 6),
        "percentage_point_change": round(rise * 100, 2),
        "ratio": round(multiple, 4) if multiple is not None else None,
        "window": {"start": obs.bucket_start.isoformat(), "end": obs.bucket_end.isoformat()},
        "metric_identity": d.semantics(obs.breakdowns),
        "measurement_completeness": {
            "breakdown": d.breakdown,
            "breakdown_status": status.value,
            "request_count_measurement": obs.measurement.value if obs.measurement else None,
        },
        "volume": _volume_context(ctx, obs),
        "contributors": contributors(obs, min_top_requests=thr.concentration_min_client_requests),
        "provenance": {"record": "waf_source_bucket", "bucket_start": obs.bucket_start.isoformat(),
                       "breakdown": d.breakdown},
        "observed_measurement": measurement.value,
        "absolute_delta": round(rise, 6),
        "caveat": d.caveat,
        **_caveat_message(d.caveat_ref),
    }

    if rise < thr.ratio_min_increase:
        return g.not_proven(
            _ref(
                "ratio.rise_below_min",
                observed=observed, rise=rise, expected=expected, minimum=thr.ratio_min_increase,
            ),
            observed=observed, expected=expected,
        )
    if multiple is not None and multiple < thr.ratio_min_multiple:
        return g.not_proven(
            _ref(
                "ratio.multiple_below_min",
                observed=observed, multiple=multiple, expected=expected,
                minimum=thr.ratio_min_multiple,
            ),
            observed=observed, expected=expected,
        )
    passed, basis, magnitude = _surprise(observed, cell, thr, facts)
    if not passed:
        return g.not_proven(basis, observed=observed, expected=expected)

    name = ("fired_lower" if g.lower_bound else "fired") + (
        "_no_multiple" if multiple is None else ""
    )
    fired = _ref(
        f"ratio.{name}",
        count=k, total=n, denominator=d.denominator_phrase, subject=_subject(ctx),
        numerator=d.numerator_phrase, share=observed, expected=expected,
        history=cell.denominator_sum, rise=rise,
        **({"multiple": multiple} if multiple is not None else {}),
        basis=basis,
    )
    reason = render(fired)
    severity = Severity.HIGH if (
        rise >= 2 * thr.ratio_min_increase
        and (multiple is None or multiple >= 2 * thr.ratio_min_multiple)
    ) else Severity.MEDIUM
    return _result(d.detector_type, AnomalyEvidence(
        anomaly_type=d.detector_type,
        signal=DetectorSignal.ANOMALOUS,
        severity=severity,
        observed_value=round(observed, 6),
        expected_value=round(expected, 6),
        baseline_low=cell.p05,
        baseline_high=cell.p95,
        deviation_score=facts.get("robust_z"),
        threshold=thr.deviation_z,
        sample_count=cell.sample_count,
        interval_start=obs.bucket_start,
        interval_end=obs.bucket_end,
        confidence=_confidence(cell, magnitude, thr),
        reason=reason,
        reason_message=fired,
        extra=facts,
    ))


def detect_block_ratio_spike(ctx: WafDetectionContext) -> WafSpikeVerdict:
    return detect_ratio(ctx, BLOCK_RATIO)


def detect_error_ratio_spike(ctx: WafDetectionContext) -> WafSpikeVerdict:
    return detect_ratio(ctx, SERVER_ERROR_RATIO)


def detect_client_error_ratio_spike(ctx: WafDetectionContext) -> WafSpikeVerdict:
    return detect_ratio(ctx, CLIENT_ERROR_RATIO)


# --------------------------------------------------------------- concentration
def detect_client_concentration_spike(ctx: WafDetectionContext) -> WafSpikeVerdict:
    """The largest source IP's share of an endpoint's requests rose.

    Independent of volume: the share can rise while total requests are normal,
    and a volume spike spread evenly across source IPs leaves it unchanged. The
    evidence reports the endpoint's volume against its own baseline so the two
    are never conflated.
    """
    dt = WafAnomalyType.WAF_CLIENT_CONCENTRATION_SPIKE
    g = _Guard(ctx, dt)
    obs, thr = g.obs, ctx.behavior
    assert obs is not None and thr is not None, "the engine supplies observation and thresholds"
    status = obs.breakdowns.client
    if not _measured(status):
        return g.insufficient(_unavailable("SOURCE_IP", status))
    if ctx.effective_scope is not WafVerdictScope.FULL:
        return g.insufficient(
            scope_reason_ref(ctx.scope_reason) or _ref("scope.composition_exact")
        )
    if obs.endpoint_coverage_ratio < thr.min_coverage:
        return g.insufficient(_low_source_coverage(obs.endpoint_coverage_ratio, thr.min_coverage))

    top1 = obs.counts.top1_client_requests or 0
    top5 = obs.counts.top5_client_requests or 0
    n = obs.counts.client_events or 0
    if obs.request_count > 0 and min(1.0, n / obs.request_count) < thr.min_coverage:
        return g.insufficient(
            _ref(
                "concentration.attributed_low",
                covered=min(1.0, n / obs.request_count), floor=thr.min_coverage,
            )
        )
    cell_or_exit = g.baseline(TOP_CLIENT_SHARE_METRIC)
    if isinstance(cell_or_exit, WafSpikeVerdict):
        return cell_or_exit
    cell = cell_or_exit
    expected = (cell.numerator_sum or 0.0) / (cell.denominator_sum or 1.0)

    if top1 < thr.concentration_min_client_requests:
        return g.not_proven(
            _ref(
                "concentration.top_below_min",
                top=top1, minimum=thr.concentration_min_client_requests,
            ),
            observed=top1 / n if n else 0.0, expected=expected,
        )
    if n < thr.concentration_min_endpoint_requests:
        return g.insufficient(
            _ref(
                "concentration.endpoint_below_min",
                count=n, minimum=thr.concentration_min_endpoint_requests,
            )
        )
    share = top1 / n
    rise = share - expected
    multiple = rstats.ratio(share, expected)
    facts: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "detector_type": dt.value,
        "entity": _entity(ctx),
        "observed": {
            "metric": TOP_CLIENT_SHARE_METRIC,
            "proportion": round(share, 6),
            "numerator": top1,
            "numerator_label": "requests from the largest source IP",
            "denominator": n,
            "denominator_label": "requests attributed to a source IP",
            "top5_share": round(top5 / n, 6),
            "distinct_sources": obs.counts.client_sources,
            "measurement": {"numerator": "EXACT", "denominator": "EXACT"},
        },
        "expected": _cell_facts(cell, expected),
        "absolute_deviation": round(rise, 6),
        "percentage_point_change": round(rise * 100, 2),
        "ratio": round(multiple, 4) if multiple is not None else None,
        "window": {"start": obs.bucket_start.isoformat(), "end": obs.bucket_end.isoformat()},
        "metric_identity": obs.breakdowns.client_semantics,
        "measurement_completeness": {
            "breakdown": "SOURCE_IP",
            "breakdown_status": status.value,
            "request_count_measurement": obs.measurement.value if obs.measurement else None,
        },
        "volume": _volume_context(ctx, obs),
        "contributors": contributors(obs, min_top_requests=thr.concentration_min_client_requests),
        "client_identity": (
            "QRadar's normalized source IP. Behind a proxy, load balancer or NAT it is the "
            "intermediary's address, not the end user's."
        ),
        "provenance": {"record": "waf_source_bucket", "bucket_start": obs.bucket_start.isoformat(),
                       "breakdown": "SOURCE_IP"},
        "observed_measurement": "EXACT",
        "absolute_delta": round(rise, 6),
        "caveat": _CAVEAT_CONCENTRATION,
        **_caveat_message(_CAVEAT_CONCENTRATION_REF),
    }
    if rise < thr.concentration_min_increase:
        return g.not_proven(
            _ref(
                "concentration.rise_below_min",
                share=share, rise=rise, expected=expected,
                minimum=thr.concentration_min_increase,
            ),
            observed=share, expected=expected,
        )
    passed, basis, magnitude = _surprise(share, cell, thr, facts)
    if not passed:
        return g.not_proven(basis, observed=share, expected=expected)

    volume = facts["volume"]
    volume_ref = (
        _ref(
            "concentration.volume_with_baseline",
            requests=obs.request_count, expected=volume["expected_requests"],
        )
        if volume["expected_requests"] is not None
        else _ref("concentration.volume_no_baseline", requests=obs.request_count)
    )
    fired = _ref(
        "concentration.fired",
        top=top1, total=n, subject=_subject(ctx), share=share, expected=expected, rise=rise,
        top5=top5 / n, volume=volume_ref, basis=basis,
    )
    reason = render(fired)
    return _result(dt, AnomalyEvidence(
        anomaly_type=dt,
        signal=DetectorSignal.ANOMALOUS,
        severity=Severity.HIGH if rise >= 2 * thr.concentration_min_increase else Severity.MEDIUM,
        observed_value=round(share, 6),
        expected_value=round(expected, 6),
        baseline_low=cell.p05,
        baseline_high=cell.p95,
        deviation_score=facts.get("robust_z"),
        threshold=thr.deviation_z,
        sample_count=cell.sample_count,
        interval_start=obs.bucket_start,
        interval_end=obs.bucket_end,
        confidence=_confidence(cell, magnitude, thr),
        reason=reason,
        reason_message=fired,
        extra=facts,
    ))


# --------------------------------------------------------------------- novelty
def detect_new_endpoint_access(ctx: WafDetectionContext) -> WafSpikeVerdict:
    """Material traffic to an endpoint absent from a verified history.

    The claim is exactly this, and no more: in every bucket of the history window
    before the endpoint's first appearance -- each recorded, with whole discovery
    and whole admission, under the same profile and normalizer -- the endpoint
    was not requested often enough in any one bucket to be admitted. Admission
    is how an endpoint becomes an entity, so an endpoint requested at or above
    the admission threshold in such a bucket would already exist. "Never
    requested" is not claimed: traffic below the threshold is never recorded.
    """
    dt = WafAnomalyType.WAF_NEW_ENDPOINT_ACCESS
    g = _Guard(ctx, dt)
    obs, thr, novelty = g.obs, ctx.behavior, ctx.novelty
    assert obs is not None and thr is not None, "the engine supplies observation and thresholds"
    if not isinstance(novelty, NoveltyFacts):
        return g.insufficient(_ref("novelty.not_established"))
    if not novelty.within_window:
        if novelty.history_end > novelty.first_seen_at:
            # The engine carries the source's history hand-off in ``history_end``; an
            # endpoint first seen before it is established whatever its age. The reason
            # names both real times and never invents an older first sighting.
            return _established(
                ctx, _ref(
                    "novelty.established_hand_off",
                    first_seen=novelty.first_seen_at, hand_off=novelty.history_end,
                ),
            )
        return _established(
            ctx, _ref(
                "novelty.established_outside_window",
                first_seen=novelty.first_seen_at, buckets=thr.new_endpoint_window_buckets,
            ),
        )
    if ctx.effective_scope is WafVerdictScope.NONE:
        return g.insufficient(_cannot_establish(ctx, novelty))
    if novelty.appearance_flood is not None:
        return g.insufficient(novelty.appearance_flood)

    lower_bound = g.lower_bound or novelty.lower_bound
    requests, buckets = novelty.requests_since_first_seen, novelty.buckets_with_traffic
    facts: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "detector_type": dt.value,
        "entity": _entity(ctx),
        "observed": {
            "metric": "requests_since_first_appearance",
            "requests": requests,
            "buckets_with_traffic": buckets,
            "first_seen_at": novelty.first_seen_at.isoformat(),
            "measurement": {
                "requests": (
                    WafMeasurement.LOWER_BOUND if lower_bound else WafMeasurement.EXACT
                ).value
            },
        },
        "expected": {
            "claim": (
                f"not requested at or above {thr.admission_min_requests} requests in any one "
                "bucket of the history window"
            ),
            "history_start": novelty.history_start.isoformat(),
            "history_end": novelty.history_end.isoformat(),
            "history_buckets_verified": novelty.history_buckets,
        },
        "materiality": {
            "min_requests": thr.new_endpoint_min_requests,
            "min_buckets": thr.new_endpoint_min_buckets,
            "window_end": novelty.window_end.isoformat(),
        },
        "window": {"start": novelty.first_seen_at.isoformat(), "end": obs.bucket_end.isoformat()},
        "metric_identity": obs.metric_semantics,
        "measurement_completeness": {
            "history": "COMPLETE",
            "request_count_measurement": obs.measurement.value if obs.measurement else None,
        },
        "contributors": contributors(obs, min_top_requests=thr.concentration_min_client_requests),
        "provenance": {
            "record": "waf_endpoint", "first_seen_at": novelty.first_seen_at.isoformat()
        },
        "observed_measurement": (
            WafMeasurement.LOWER_BOUND if lower_bound else WafMeasurement.EXACT
        ).value,
        "absolute_delta": float(requests),
        "caveat": _CAVEAT_NEW_ENDPOINT,
        **_caveat_message(_CAVEAT_NEW_ENDPOINT_REF),
    }
    if requests < thr.new_endpoint_min_requests or buckets < thr.new_endpoint_min_buckets:
        short = _ref(
            "novelty.below_materiality",
            requests=requests, buckets=buckets,
            min_requests=thr.new_endpoint_min_requests, min_buckets=thr.new_endpoint_min_buckets,
        )
        if lower_bound:
            return g.insufficient(_ref("novelty.below_materiality_lower", reason=short))
        text, message = split_reason(short)
        return _result(dt, healthy(
            dt, interval_start=obs.bucket_start, interval_end=obs.bucket_end,
            observed=float(requests), expected=0.0, reason=text, reason_message=message,
        ))
    fired = _ref(
        "novelty.fired_lower" if lower_bound else "novelty.fired",
        subject=_subject(ctx), requests=requests, buckets=buckets,
        first_seen=novelty.first_seen_at, history_start=novelty.history_start,
        history_end=novelty.history_end, admission=thr.admission_min_requests,
    )
    reason = render(fired)
    return _result(dt, AnomalyEvidence(
        anomaly_type=dt,
        signal=DetectorSignal.ANOMALOUS,
        severity=Severity.MEDIUM,
        observed_value=float(requests),
        expected_value=0.0,
        baseline_low=None,
        baseline_high=None,
        deviation_score=None,
        threshold=float(thr.new_endpoint_min_requests),
        sample_count=novelty.history_buckets,
        interval_start=obs.bucket_start,
        interval_end=obs.bucket_end,
        confidence=0.8,
        reason=reason,
        reason_message=fired,
        extra=facts,
    ))


def _established(ctx: WafDetectionContext, reason: MessageRef) -> WafSpikeVerdict:
    """The healthy verdict of an endpoint that was already established."""
    obs = ctx.observation
    dt = WafAnomalyType.WAF_NEW_ENDPOINT_ACCESS
    return _result(dt, healthy(
        dt, interval_start=obs.bucket_start, interval_end=obs.bucket_end,
        observed=None, expected=None, reason=render(reason), reason_message=reason,
    ))


def _cannot_establish(ctx: WafDetectionContext, novelty: NoveltyFacts) -> MessageRef:
    """Why novelty cannot be established: the history's reason, else the scope's, else a default."""
    history = novelty.history
    cause: Reason
    if history.reason:
        message = getattr(history, "message", None)
        cause = _paired(history.reason, message)
    elif ctx.scope_reason:
        cause = scope_reason_ref(ctx.scope_reason) or ctx.scope_reason
    else:
        cause = _ref("novelty.history_incomplete")
    return _ref("novelty.cannot_establish", cause=cause)


def _confidence(cell: WafBaselineCell, magnitude: float, thr: WafBehaviorThresholds) -> float:
    reliability = min(1.0, cell.sample_count / 30.0)
    strength = min(1.0, abs(magnitude) / (2 * thr.deviation_z))
    return round((0.4 * reliability + 0.6 * strength) * min(1.0, cell.completeness + 0.5), 3)
