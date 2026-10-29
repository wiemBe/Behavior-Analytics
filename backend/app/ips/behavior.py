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
from app.i18n.messages import MessageRef, render
from app.ips.detector import (
    EVIDENCE_VERSION,
    IpsDetectionContext,
    IpsNoveltyFacts,
    IpsVerdict,
    _anomalous,
    _cell_facts,
    _contributors,
    _Guard,
    _is_unusable_port_key,
    _ref,
    _score,
    _spike,
    caveat_facts,
    detect_destination_rate_spike,
    detect_port_rate_spike,
    detect_signature_burst,
    state_ref,
)
from app.ips.inputs import (
    IPS_BLOCK_RATIO_METRIC,
    IPS_FANOUT_METRIC,
    IPS_TOP_SOURCE_SHARE_METRIC,
    IpsBucketMeasurement,
    IpsSourceBucketFacts,
    ips_history_incomplete,
)
from app.models.enums import (
    DetectorSignal,
    IpsAnomalyType,
    IpsDimension,
    Severity,
    WafMeasurement,
)
from app.waf.detector import Reason, WafBaselineCell, _paired
from app.waf.inputs import HistoryFacts


def detect_port_fanout_spike(ctx: IpsDetectionContext) -> IpsVerdict:
    """One source IP reached more distinct destination ports than its baseline allows."""
    dt = IpsAnomalyType.IPS_PORT_FANOUT_SPIKE
    g = _Guard(ctx, dt)
    bucket, thresholds = g.bucket, g.thr
    if (early := g.maintenance()) is not None:
        return early
    if (
        early := g.dimension(
            IpsDimension.PORT_FANOUT, _ref("label.port_fanout"), coverage=False
        )
    ) is not None:
        return early
    # The fan-out query excludes absent destination ports. Its maximum is not a
    # whole observation when too little source traffic carried a usable port.
    if (
        early := g.dimension(
            IpsDimension.DESTINATION_PORT, _ref("label.destination_port_hyphenated")
        )
    ) is not None:
        return early
    if bucket.max_distinct_ports is None:
        return g.insufficient(_ref("guard.fanout_unstated"))
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
        **caveat_facts(_ref("caveat.port_fanout")),
        **ctx.extra,
    }
    return _spike(
        g,
        cell=cell,
        observed=observed,
        subject=_ref("subject.busiest_fanout"),
        unit=_ref("noun.distinct_ports"),
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
    if (
        early := g.dimension(IpsDimension.SOURCE_IP, _ref("label.source_ip_hyphenated"))
    ) is not None:
        return early
    if bucket.top1_events is None or bucket.source_events is None:
        return g.insufficient(_ref("guard.source_totals_unstated"))
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
            _ref(
                "concentration.top_below_min",
                top=top1, minimum=thresholds.concentration_min_top1_events,
            ),
            observed=top1 / total if total else 0.0,
            expected=expected,
        )
    if total < thresholds.concentration_min_source_events:
        return g.insufficient(
            _ref(
                "concentration.total_below_min",
                total=total, minimum=thresholds.concentration_min_source_events,
            )
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
    facts.update(caveat_facts(_ref("caveat.source_concentration")))
    if rise < thresholds.concentration_min_increase:
        return g.not_proven(
            MessageRef(
                "waf.concentration.rise_below_min",
                {
                    "share": share, "rise": rise, "expected": expected,
                    "minimum": thresholds.concentration_min_increase,
                },
            ),
            observed=share,
            expected=expected,
        )
    passed, basis, magnitude = _score(share, cell, thresholds, facts, events=False)
    if not passed:
        return g.not_proven(basis, observed=share, expected=expected)
    figures: dict[str, object] = {
        "top": top1, "total": total, "share": share, "expected": expected, "rise": rise,
        "basis": basis,
    }
    if bucket.top5_events is not None:
        fired = _ref("concentration.fired", top5=bucket.top5_events / total, **figures)
    else:
        fired = _ref("concentration.fired_no_top5", **figures)
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
        reason=fired,
    )


def detect_block_ratio_shift(ctx: IpsDetectionContext) -> IpsVerdict:
    """The blocked share of exact action events rose above its pooled baseline."""
    dt = IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT
    g = _Guard(ctx, dt)
    bucket, thresholds = g.bucket, g.thr
    if (early := g.maintenance()) is not None:
        return early
    if bucket.action.measurement is IpsBucketMeasurement.NOT_COLLECTED:
        return g.insufficient(_ref("guard.action_unmapped"))
    if (early := g.dimension(IpsDimension.ACTION, _ref("label.action"))) is not None:
        return early
    blocked, total = bucket.blocked_events, bucket.action_events
    if blocked is None or total is None:
        return g.insufficient(_ref("guard.action_counts_unstated"))
    if (early := g.scope()) is not None:
        return early
    cell_or_exit = g.cell(IPS_BLOCK_RATIO_METRIC, pooled=True)
    if isinstance(cell_or_exit, IpsVerdict):
        return cell_or_exit
    cell = cell_or_exit
    expected = (cell.numerator_sum or 0.0) / (cell.denominator_sum or 1.0)

    if blocked < thresholds.ratio_min_numerator:
        return g.not_proven(
            _ref(
                "block.numerator_below_min",
                count=blocked, minimum=thresholds.ratio_min_numerator,
            ),
            observed=blocked / total if total else 0.0,
            expected=expected,
        )
    if total < thresholds.ratio_min_denominator:
        return g.insufficient(
            _ref(
                "block.denominator_below_min",
                count=total, minimum=thresholds.ratio_min_denominator,
            )
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
    facts.update(caveat_facts(_ref("caveat.block_ratio")))
    if rise < thresholds.ratio_min_increase:
        return g.not_proven(
            MessageRef(
                "waf.ratio.rise_below_min",
                {
                    "observed": observed, "rise": rise, "expected": expected,
                    "minimum": thresholds.ratio_min_increase,
                },
            ),
            observed=observed,
            expected=expected,
        )
    if multiple is not None and multiple < thresholds.ratio_min_multiple:
        return g.not_proven(
            MessageRef(
                "waf.ratio.multiple_below_min",
                {
                    "observed": observed, "multiple": multiple, "expected": expected,
                    "minimum": thresholds.ratio_min_multiple,
                },
            ),
            observed=observed,
            expected=expected,
        )
    passed, basis, magnitude = _score(observed, cell, thresholds, facts, events=False)
    if not passed:
        return g.not_proven(basis, observed=observed, expected=expected)
    figures = {
        "count": blocked, "total": total, "share": observed, "expected": expected,
        "history": cell.denominator_sum, "rise": rise, "basis": basis,
    }
    if multiple is not None:
        figures["multiple"] = multiple
    suffix = "" if multiple is not None else "_no_multiple"
    fired = _ref(f"block.fired{'_lower' if g.lower_bound else ''}{suffix}", **figures)
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
        reason=fired,
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
        return ips_history_incomplete("waf.history_check.not_divisible")
    expected_count = window_seconds // bucket_seconds
    expected_starts = {
        history_start + timedelta(seconds=bucket_seconds * index)
        for index in range(expected_count)
    }
    starts = {bucket.bucket_start for bucket in buckets}
    missing = len(expected_starts - starts)
    if missing:
        return ips_history_incomplete(
            "waf.history_check.never_collected", missing=missing, expected=expected_count
        )
    if len(buckets) != expected_count:
        return ips_history_incomplete(
            "waf.history_check.record_count", records=len(buckets), expected=expected_count
        )
    ordered = sorted(buckets, key=lambda bucket: bucket.bucket_start)

    checks: list[
        tuple[
            Callable[[IpsSourceBucketFacts], bool],
            Callable[[IpsSourceBucketFacts], MessageRef],
        ]
    ] = [
        (
            lambda bucket: bucket.bucket_seconds != bucket_seconds,
            lambda bucket: MessageRef(
                "waf.history_check.bucket_width", {"bucket": bucket.bucket_start}
            ),
        ),
        (
            lambda bucket: (
                bucket.destination_port.measurement is not IpsBucketMeasurement.EXACT
            ),
            lambda bucket: _ref(
                "history_check.port_search_not_exact",
                bucket=bucket.bucket_start, state=state_ref(bucket.destination_port.state),
            ),
        ),
        (
            lambda bucket: not bucket.destination_port.coverage_met(min_coverage),
            lambda bucket: _ref(
                "history_check.coverage_low", bucket=bucket.bucket_start, floor=min_coverage
            ),
        ),
        (
            lambda bucket: bucket.destination_port.admission_truncated is not False,
            lambda bucket: _ref("history_check.admission_unrecorded", bucket=bucket.bucket_start),
        ),
        (
            lambda bucket: bucket.profile_version != profile_version,
            lambda bucket: MessageRef(
                "waf.history_check.profile_version",
                {
                    "bucket": bucket.bucket_start,
                    "found": str(bucket.profile_version),
                    "expected": profile_version,
                },
            ),
        ),
        (
            lambda bucket: bucket.normalizer_version != normalizer_version,
            lambda bucket: _ref(
                "history_check.normalizer_version",
                bucket=bucket.bucket_start, version=normalizer_version,
            ),
        ),
        (
            lambda bucket: bucket.in_maintenance,
            lambda bucket: _ref("history_check.maintenance", bucket=bucket.bucket_start),
        ),
    ]
    for failing, wording in checks:
        for bucket in ordered:
            if failing(bucket):
                message = wording(bucket)
                return ips_history_incomplete(message.code, **message.params)
    return HistoryFacts(complete=True)


def appearance_flood_ref(bucket: IpsSourceBucketFacts, *, min_coverage: float) -> MessageRef | None:
    """Why the first-appearance bucket cannot isolate a newly admitted port."""
    port = bucket.destination_port
    if port.measurement is not IpsBucketMeasurement.EXACT or not port.coverage_met(min_coverage):
        return _ref("novelty.flood_discovery")
    if port.admission_truncated is not False:
        return _ref("novelty.flood_admission")
    return None


def appearance_flood(bucket: IpsSourceBucketFacts, *, min_coverage: float) -> str | None:
    """`appearance_flood_ref` as its English sentence."""
    flood = appearance_flood_ref(bucket, min_coverage=min_coverage)
    return render(flood) if flood is not None else None


def _cause(novelty: IpsNoveltyFacts) -> Reason:
    """Why novelty cannot be established: the history's reason, else a default."""
    history = novelty.history
    if history.reason:
        return _paired(history.reason, getattr(history, "message", None))
    return MessageRef("waf.novelty.history_incomplete")


def detect_new_destination_port(ctx: IpsDetectionContext) -> IpsVerdict:
    """Material traffic on a port below admission throughout a verified history."""
    dt = IpsAnomalyType.IPS_NEW_DESTINATION_PORT
    g = _Guard(ctx, dt)
    observation, thresholds, novelty = g.obs, g.thr, ctx.novelty
    if (early := g.maintenance()) is not None:
        return early
    entity = observation.entity
    if entity is None or entity.dimension is not IpsDimension.DESTINATION_PORT:
        return g.insufficient(
            _ref("guard.no_entity", label=_ref("label.destination_port_hyphenated"))
        )
    if _is_unusable_port_key(entity.value_key):
        return g.insufficient(_ref("guard.absent_port"))
    if novelty is None:
        return g.insufficient(_ref("novelty.not_established"))
    if not novelty.within_window:
        return g.healthy(
            _ref(
                "novelty.established_outside_window",
                first_seen=novelty.first_seen_at, buckets=thresholds.new_port_window_buckets,
            ),
            observed=None,
            expected=None,
        )
    if novelty.readmitted:
        return g.healthy(_ref("novelty.readmitted"), observed=None, expected=None)
    if (
        early := g.dimension(
            IpsDimension.DESTINATION_PORT, _ref("label.destination_port_hyphenated")
        )
    ) is not None:
        return early
    if not novelty.history.complete:
        return g.insufficient(_ref("novelty.cannot_establish", cause=_cause(novelty)))
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
        **caveat_facts(_ref("caveat.new_port")),
        **ctx.extra,
    }
    if events < thresholds.new_port_min_events or buckets < thresholds.new_port_min_buckets:
        short = _ref(
            "novelty.below_materiality",
            events=events, buckets=buckets,
            min_events=thresholds.new_port_min_events, min_buckets=thresholds.new_port_min_buckets,
        )
        if lower_bound:
            return g.insufficient(_ref("novelty.below_materiality_lower", reason=short))
        return g.healthy(short, observed=float(events), expected=0.0)
    fired = _ref(
        "novelty.fired_lower" if lower_bound else "novelty.fired",
        port=entity.display_name or entity.value_key, events=events, buckets=buckets,
        first_seen=novelty.first_seen_at, history_start=novelty.history_start,
        history_end=novelty.history_end, admission=thresholds.admission_min_events,
    )
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
            reason=render(fired),
            reason_message=fired,
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
