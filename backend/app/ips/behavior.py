"""Source-scoped IPS detectors and destination-port novelty verification.

This is the second half of the pure IPS detector layer. Shared evidence and
guard mechanics, plus the entity-rate detectors, live in `app.ips.detector`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from app.anomaly import statistics as rstats
from app.anomaly.evidence import AnomalyEvidence
from app.core.text import percent, points
from app.ips.detector import (
    EVIDENCE_VERSION,
    IpsDetectionContext,
    IpsVerdict,
    _anomalous,
    _cell_facts,
    _contributors,
    _Guard,
    _is_unusable_port_key,
    _pct,
    _score,
    _spike,
    detect_destination_rate_spike,
    detect_port_rate_spike,
    detect_signature_burst,
)
from app.ips.inputs import (
    IPS_BLOCK_RATIO_METRIC,
    IPS_FANOUT_METRIC,
    IPS_TOP_SOURCE_SHARE_METRIC,
    IpsBucketMeasurement,
    IpsSourceBucketFacts,
)
from app.models.enums import (
    DetectorSignal,
    IpsAnomalyType,
    IpsDimension,
    Severity,
    WafMeasurement,
)
from app.waf.detector import WafBaselineCell
from app.waf.inputs import HistoryFacts


def detect_port_fanout_spike(ctx: IpsDetectionContext) -> IpsVerdict:
    """One source IP reached more distinct destination ports than its baseline allows."""
    dt = IpsAnomalyType.IPS_PORT_FANOUT_SPIKE
    g = _Guard(ctx, dt)
    bucket, thresholds = g.bucket, g.thr
    if (early := g.maintenance()) is not None:
        return early
    if (
        early := g.dimension(IpsDimension.PORT_FANOUT, "port fan-out", coverage=False)
    ) is not None:
        return early
    # The fan-out query excludes absent destination ports. Its maximum is not a
    # whole observation when too little source traffic carried a usable port.
    if (
        early := g.dimension(IpsDimension.DESTINATION_PORT, "destination-port")
    ) is not None:
        return early
    if bucket.max_distinct_ports is None:
        return g.insufficient("the bucket does not state its largest distinct-port count")
    if (early := g.scope()) is not None:
        return early
    cell_or_exit = g.cell(IPS_FANOUT_METRIC)
    if isinstance(cell_or_exit, IpsVerdict):
        return cell_or_exit
    cell = cell_or_exit
    observed = float(bucket.max_distinct_ports)
    measurement = bucket.port_fanout.waf_measurement
    assert measurement is not None, "the measurement guard ran before evidence construction"
    facts: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "detector_type": dt.value,
        "observed": {
            "metric": IPS_FANOUT_METRIC,
            "max_distinct_ports": bucket.max_distinct_ports,
            "measurement": measurement.value,
        },
        "expected": _cell_facts(cell, cell.median),
        "absolute_delta": round(observed - cell.median, 3),
        "window": {"start": g.start.isoformat(), "end": g.end.isoformat()},
        "contributors": _contributors(
            bucket, IpsDimension.PORT_FANOUT.value, "distinct_ports", total=None
        ),
        "provenance": {
            "record": "ips_source_bucket",
            "bucket_start": bucket.bucket_start.isoformat(),
        },
        "observed_measurement": measurement.value,
        "caveat": (
            "Many distinct destination ports from one source IP is a traffic shape, not an "
            "attack verdict: a scan, a vulnerability assessment and a service that rotates "
            "ports look alike."
        ),
        **ctx.extra,
    }
    return _spike(
        g,
        cell=cell,
        observed=observed,
        subject="the busiest source IP's distinct ports",
        unit="distinct ports",
        min_value=thresholds.fanout_min_ports,
        ratio_min=thresholds.fanout_ratio,
        min_delta=thresholds.fanout_min_delta_ports,
        facts=facts,
        observed_lower_bound=measurement is WafMeasurement.LOWER_BOUND,
    )


def _proportion_facts(
    g: _Guard,
    *,
    metric: str,
    k: int,
    n: int,
    k_label: str,
    n_label: str,
    cell: WafBaselineCell,
    expected: float,
    k_measurement: WafMeasurement,
) -> dict[str, Any]:
    observed = k / n
    rise = observed - expected
    multiple = rstats.ratio(observed, expected)
    return {
        "evidence_version": EVIDENCE_VERSION,
        "detector_type": g.dt.value,
        "observed": {
            "metric": metric,
            "proportion": round(observed, 6),
            "numerator": k,
            "numerator_label": k_label,
            "denominator": n,
            "denominator_label": n_label,
            "measurement": {
                "numerator": k_measurement.value,
                "denominator": WafMeasurement.EXACT.value,
            },
        },
        "expected": _cell_facts(cell, expected),
        "absolute_deviation": round(rise, 6),
        "percentage_point_change": round(rise * 100, 2),
        "ratio": round(multiple, 4) if multiple is not None else None,
        "window": {"start": g.start.isoformat(), "end": g.end.isoformat()},
        "provenance": {
            "record": "ips_source_bucket",
            "bucket_start": g.bucket.bucket_start.isoformat(),
        },
        "observed_measurement": k_measurement.value,
        "absolute_delta": round(rise, 6),
        **g.ctx.extra,
    }


def detect_source_concentration_spike(ctx: IpsDetectionContext) -> IpsVerdict:
    """The exact top-1 source IP share rose above its pooled seasonal baseline."""
    dt = IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE
    g = _Guard(ctx, dt)
    bucket, thresholds = g.bucket, g.thr
    if (early := g.maintenance()) is not None:
        return early
    if (early := g.dimension(IpsDimension.SOURCE_IP, "source-IP")) is not None:
        return early
    if bucket.top1_events is None or bucket.source_events is None:
        return g.insufficient("the bucket does not state its source-IP totals")
    if (early := g.scope()) is not None:
        return early
    cell_or_exit = g.cell(IPS_TOP_SOURCE_SHARE_METRIC, pooled=True)
    if isinstance(cell_or_exit, IpsVerdict):
        return cell_or_exit
    cell = cell_or_exit
    expected = (cell.numerator_sum or 0.0) / (cell.denominator_sum or 1.0)
    top1, total = bucket.top1_events, bucket.source_events

    if top1 < thresholds.concentration_min_top1_events:
        return g.not_proven(
            f"the largest source IP sent {top1} events, below the "
            f"{thresholds.concentration_min_top1_events} minimum for a material concentration",
            observed=top1 / total if total else 0.0,
            expected=expected,
        )
    if total < thresholds.concentration_min_source_events:
        return g.insufficient(
            f"{total} events is below the {thresholds.concentration_min_source_events} minimum "
            "for a composition verdict"
        )
    share = top1 / total
    rise = share - expected
    facts = _proportion_facts(
        g,
        metric=IPS_TOP_SOURCE_SHARE_METRIC,
        k=top1,
        n=total,
        k_label="events from the largest source IP",
        n_label="events carrying a source IP",
        cell=cell,
        expected=expected,
        k_measurement=WafMeasurement.EXACT,
    )
    facts["observed"]["top5_share"] = (
        round(bucket.top5_events / total, 6) if bucket.top5_events is not None else None
    )
    facts["observed"]["distinct_sources"] = bucket.distinct_sources
    facts["contributors"] = _contributors(
        bucket, IpsDimension.SOURCE_IP.value, "events", total=total
    )
    facts["caveat"] = (
        "A dominant source IP describes traffic composition, not intent. Behind a proxy or "
        "NAT it is the intermediary's address."
    )
    if rise < thresholds.concentration_min_increase:
        return g.not_proven(
            f"the largest source IP's share {_pct(share)} is {points(rise)} from the "
            f"expected {_pct(expected)}, below the "
            f"{points(thresholds.concentration_min_increase, precision=0, signed=False)} "
            "minimum rise",
            observed=share,
            expected=expected,
        )
    passed, basis, magnitude = _score(share, cell, thresholds, facts, _pct)
    if not passed:
        return g.not_proven(basis, observed=share, expected=expected)
    return _anomalous(
        g,
        cell=cell,
        observed=share,
        expected=expected,
        z=facts.get("robust_z"),
        magnitude=magnitude,
        threshold=thresholds.deviation_z,
        facts=facts,
        severity=(
            Severity.HIGH
            if rise >= 2 * thresholds.concentration_min_increase
            else Severity.MEDIUM
        ),
        reason=(
            f"the largest source IP sent {top1} of {total} events ({_pct(share)}), against an "
            f"expected share of {_pct(expected)} ({points(rise)}); the five largest "
            f"sent {_pct(bucket.top5_events / total if bucket.top5_events is not None else None)}; "
            f"{basis}"
        ),
    )


def detect_block_ratio_shift(ctx: IpsDetectionContext) -> IpsVerdict:
    """The blocked share of exact action events rose above its pooled baseline."""
    dt = IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT
    g = _Guard(ctx, dt)
    bucket, thresholds = g.bucket, g.thr
    if (early := g.maintenance()) is not None:
        return early
    if bucket.action.measurement is IpsBucketMeasurement.NOT_COLLECTED:
        return g.insufficient("no action property mapped")
    if (early := g.dimension(IpsDimension.ACTION, "action")) is not None:
        return early
    blocked, total = bucket.blocked_events, bucket.action_events
    if blocked is None or total is None:
        return g.insufficient("the bucket does not state its blocked and action event counts")
    if (early := g.scope()) is not None:
        return early
    cell_or_exit = g.cell(IPS_BLOCK_RATIO_METRIC, pooled=True)
    if isinstance(cell_or_exit, IpsVerdict):
        return cell_or_exit
    cell = cell_or_exit
    expected = (cell.numerator_sum or 0.0) / (cell.denominator_sum or 1.0)

    if blocked < thresholds.ratio_min_numerator:
        return g.not_proven(
            f"{blocked} blocked events is below the {thresholds.ratio_min_numerator} minimum; "
            "no material rise in the proportion is possible",
            observed=blocked / total if total else 0.0,
            expected=expected,
        )
    if total < thresholds.ratio_min_denominator:
        return g.insufficient(
            f"{total} events carrying an action is below the "
            f"{thresholds.ratio_min_denominator} minimum denominator for a ratio verdict"
        )
    observed = blocked / total
    rise = observed - expected
    multiple = rstats.ratio(observed, expected)
    numerator_measurement = (
        WafMeasurement.LOWER_BOUND if g.lower_bound else WafMeasurement.EXACT
    )
    facts = _proportion_facts(
        g,
        metric=IPS_BLOCK_RATIO_METRIC,
        k=blocked,
        n=total,
        k_label="blocked events",
        n_label="events carrying an action",
        cell=cell,
        expected=expected,
        k_measurement=numerator_measurement,
    )
    facts["observed"]["unmapped"] = bucket.unmapped_events
    facts["observed"]["unmapped_label"] = "events with an action value the map does not name"
    facts["caveat"] = (
        "Blocking is judged through the profile's action map. A higher block ratio describes "
        "the IPS's decisions; it does not establish that an attack succeeded."
    )
    if rise < thresholds.ratio_min_increase:
        return g.not_proven(
            f"{_pct(observed)} is {points(rise)} from the expected {_pct(expected)}, below "
            f"the {points(thresholds.ratio_min_increase, precision=0, signed=False)} "
            "minimum rise",
            observed=observed,
            expected=expected,
        )
    if multiple is not None and multiple < thresholds.ratio_min_multiple:
        return g.not_proven(
            f"{_pct(observed)} is {multiple:.2f}x the expected {_pct(expected)}, below "
            f"{thresholds.ratio_min_multiple:.2f}x",
            observed=observed,
            expected=expected,
        )
    passed, basis, magnitude = _score(observed, cell, thresholds, facts, _pct)
    if not passed:
        return g.not_proven(basis, observed=observed, expected=expected)
    bound = "at least " if g.lower_bound else ""
    return _anomalous(
        g,
        cell=cell,
        observed=observed,
        expected=expected,
        z=facts.get("robust_z"),
        magnitude=magnitude,
        threshold=thresholds.deviation_z,
        facts=facts,
        severity=(
            Severity.HIGH
            if rise >= 2 * thresholds.ratio_min_increase
            and (multiple is None or multiple >= 2 * thresholds.ratio_min_multiple)
            else Severity.MEDIUM
        ),
        reason=(
            f"{bound}{blocked} of {total} events carrying an action were blocked "
            f"({bound}{_pct(observed)}), against an expected {_pct(expected)} from "
            f"{cell.denominator_sum:.0f} historical events ({points(rise)}"
            + (f", {multiple:.1f}x" if multiple is not None else "")
            + f"); {basis}"
        ),
    )


def verify_port_history(
    buckets: Sequence[IpsSourceBucketFacts],
    *,
    history_start: datetime,
    history_end: datetime,
    bucket_seconds: int,
    profile_version: int,
    normalizer_version: int,
    min_coverage: float,
) -> HistoryFacts:
    """Prove every history bucket can vouch that a port stayed below admission."""
    window_seconds = int((history_end - history_start).total_seconds())
    if bucket_seconds <= 0 or window_seconds <= 0 or window_seconds % bucket_seconds:
        return HistoryFacts(
            complete=False,
            reason="the history window is not divisible by the collection bucket width",
        )
    expected_count = window_seconds // bucket_seconds
    expected_starts = {
        history_start + timedelta(seconds=bucket_seconds * index)
        for index in range(expected_count)
    }
    starts = {bucket.bucket_start for bucket in buckets}
    missing = len(expected_starts - starts)
    if missing:
        return HistoryFacts(
            complete=False,
            reason=f"{missing} of {expected_count} history buckets were never collected",
        )
    if len(buckets) != expected_count:
        return HistoryFacts(
            complete=False,
            reason=(
                f"the history contains {len(buckets)} bucket records for "
                f"{expected_count} expected buckets"
            ),
        )
    ordered = sorted(buckets, key=lambda bucket: bucket.bucket_start)

    def fail(bucket: IpsSourceBucketFacts, what: str) -> HistoryFacts:
        return HistoryFacts(
            complete=False,
            reason=f"history bucket {bucket.bucket_start.isoformat()} {what}",
        )

    checks: list[
        tuple[Callable[[IpsSourceBucketFacts], bool], Callable[[IpsSourceBucketFacts], str]]
    ] = [
        (
            lambda bucket: bucket.bucket_seconds != bucket_seconds,
            lambda bucket: "used a different collection bucket width",
        ),
        (
            lambda bucket: (
                bucket.destination_port.measurement is not IpsBucketMeasurement.EXACT
            ),
            lambda bucket: (
                "had a destination-port search that was "
                f"{bucket.destination_port.state}, not exact"
            ),
        ),
        (
            lambda bucket: not bucket.destination_port.coverage_met(min_coverage),
            lambda bucket: f"had destination-port coverage below the {percent(min_coverage)} floor",
        ),
        (
            lambda bucket: bucket.destination_port.admission_truncated is not False,
            lambda bucket: "had truncated or unrecorded destination-port admission",
        ),
        (
            lambda bucket: bucket.profile_version != profile_version,
            lambda bucket: (
                f"used profile version {bucket.profile_version}, not first-appearance version "
                f"{profile_version}"
            ),
        ),
        (
            lambda bucket: bucket.normalizer_version != normalizer_version,
            lambda bucket: f"is incompatible with normalizer version {normalizer_version}",
        ),
        (lambda bucket: bucket.in_maintenance, lambda bucket: "was in maintenance"),
    ]
    for failing, wording in checks:
        for bucket in ordered:
            if failing(bucket):
                return fail(bucket, wording(bucket))
    return HistoryFacts(complete=True)


def appearance_flood(bucket: IpsSourceBucketFacts, *, min_coverage: float) -> str | None:
    """Why the first-appearance bucket cannot isolate a newly admitted port."""
    port = bucket.destination_port
    if port.measurement is not IpsBucketMeasurement.EXACT or not port.coverage_met(min_coverage):
        return (
            "the first-appearance bucket's destination-port discovery was incomplete: the port "
            "cannot be told apart from a flood of new ports"
        )
    if port.admission_truncated is not False:
        return (
            "the first-appearance bucket's destination-port admission was truncated: the port "
            "appeared in a flood of new ports and is not individually new"
        )
    return None


def detect_new_destination_port(ctx: IpsDetectionContext) -> IpsVerdict:
    """Material traffic on a port below admission throughout a verified history."""
    dt = IpsAnomalyType.IPS_NEW_DESTINATION_PORT
    g = _Guard(ctx, dt)
    observation, thresholds, novelty = g.obs, g.thr, ctx.novelty
    if (early := g.maintenance()) is not None:
        return early
    entity = observation.entity
    if entity is None or entity.dimension is not IpsDimension.DESTINATION_PORT:
        return g.insufficient("no destination-port entity was supplied for this verdict")
    if _is_unusable_port_key(entity.value_key):
        return g.insufficient(
            "port 0 and protocol 255 are absent values, not a service: no claim about them"
        )
    if novelty is None:
        return g.insufficient("the port's first appearance was not established")
    if not novelty.within_window:
        return g.healthy(
            f"established port: first admitted {novelty.first_seen_at.isoformat()}, outside the "
            f"{thresholds.new_port_window_buckets}-bucket novelty window",
            observed=None,
            expected=None,
        )
    if novelty.readmitted:
        return g.healthy(
            "re-admitted port: it was retired and seen again, so it is not new",
            observed=None,
            expected=None,
        )
    if (
        early := g.dimension(IpsDimension.DESTINATION_PORT, "destination-port")
    ) is not None:
        return early
    if not novelty.history.complete:
        return g.insufficient(
            "novelty cannot be established: "
            + (novelty.history.reason or "the history is incomplete")
        )
    if (early := g.scope()) is not None:
        return early
    if novelty.appearance_flood is not None:
        return g.insufficient(novelty.appearance_flood)

    lower_bound = g.lower_bound or novelty.lower_bound
    measurement = WafMeasurement.LOWER_BOUND if lower_bound else WafMeasurement.EXACT
    events, buckets = novelty.events_since_first_seen, novelty.buckets_with_traffic
    facts: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "detector_type": dt.value,
        "entity": {
            "dimension": entity.dimension.value,
            "value_key": entity.value_key,
            "display_name": entity.display_name,
        },
        "observed": {
            "metric": "events_since_first_appearance",
            "events": events,
            "buckets_with_traffic": buckets,
            "first_seen_at": novelty.first_seen_at.isoformat(),
            "measurement": {"events": measurement.value},
        },
        "expected": {
            "claim": (
                f"below {thresholds.admission_min_events} events in every bucket of the "
                "history window"
            ),
            "history_start": novelty.history_start.isoformat(),
            "history_end": novelty.history_end.isoformat(),
            "history_buckets_verified": novelty.history_buckets,
        },
        "materiality": {
            "min_events": thresholds.new_port_min_events,
            "min_buckets": thresholds.new_port_min_buckets,
            "window_end": novelty.window_end.isoformat(),
        },
        "window": {"start": novelty.first_seen_at.isoformat(), "end": g.end.isoformat()},
        "measurement_completeness": {"history": "COMPLETE"},
        "contributors": _contributors(
            g.bucket, IpsDimension.SOURCE_IP.value, "events", total=g.bucket.source_events
        ),
        "provenance": {
            "record": "ips_entity",
            "first_seen_at": novelty.first_seen_at.isoformat(),
        },
        "observed_measurement": measurement.value,
        "absolute_delta": float(events),
        "caveat": (
            "A newly busy destination port may be a deployment as easily as a probe. The "
            "contributors are the bucket's busiest source IPs, not necessarily this port's."
        ),
        **ctx.extra,
    }
    if events < thresholds.new_port_min_events or buckets < thresholds.new_port_min_buckets:
        reason = (
            f"{events} event(s) in {buckets} bucket(s) since first appearance has not reached "
            f"{thresholds.new_port_min_events} events in "
            f"{thresholds.new_port_min_buckets} buckets"
        )
        if lower_bound:
            return g.insufficient(f"{reason}; the counts are lower bounds")
        return g.healthy(reason, observed=float(events), expected=0.0)
    bound = "at least " if lower_bound else ""
    return g.verdict(
        AnomalyEvidence(
            anomaly_type=dt,
            signal=DetectorSignal.ANOMALOUS,
            severity=Severity.MEDIUM,
            observed_value=float(events),
            expected_value=0.0,
            baseline_low=None,
            baseline_high=None,
            deviation_score=None,
            threshold=float(thresholds.new_port_min_events),
            sample_count=novelty.history_buckets,
            interval_start=g.bucket.bucket_start,
            interval_end=g.bucket.bucket_end,
            confidence=0.8,
            reason=(
                f"destination port {entity.display_name or entity.value_key} carried "
                f"{bound}{events} events across {buckets} buckets since it first appeared at "
                f"{novelty.first_seen_at.isoformat()}; in the verified history "
                f"{novelty.history_start.isoformat()} to {novelty.history_end.isoformat()} it "
                f"stayed below {thresholds.admission_min_events} events in every bucket"
            ),
            extra=facts,
        )
    )


# Every pure detector function, by type, for the engine T-051b adds.
IPS_DETECTOR_FUNCTIONS: dict[
    IpsAnomalyType, Callable[[IpsDetectionContext], IpsVerdict]
] = {
    IpsAnomalyType.IPS_PORT_RATE_SPIKE: detect_port_rate_spike,
    IpsAnomalyType.IPS_DESTINATION_RATE_SPIKE: detect_destination_rate_spike,
    IpsAnomalyType.IPS_SIGNATURE_BURST: detect_signature_burst,
    IpsAnomalyType.IPS_NEW_DESTINATION_PORT: detect_new_destination_port,
    IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE: detect_source_concentration_spike,
    IpsAnomalyType.IPS_PORT_FANOUT_SPIKE: detect_port_fanout_spike,
    IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT: detect_block_ratio_shift,
}
