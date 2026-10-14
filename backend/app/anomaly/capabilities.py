"""Which detectors can reach a verdict, on what data, through what wiring.

`AnomalyType` names nine log-source detectors, and `detectors.py` implements all
nine as pure functions. That does not make nine of them operational. A detector
is only as real as its inputs, and the production collection path -- one bounded
Ariel aggregate per bucket -- measures event volume and the newest event time,
nothing else. Every other `LogSourceMetricSample` field keeps its DTO default,
and a detector reading `unknown_event_count=0` would report a healthy
unknown-event ratio that nobody ever measured.

This module states each detector's requirements once. The engine uses it to run
a detector only on buckets that really measured its inputs, and the API uses it
to publish the same truth, so neither code nor UI can imply a detector is
watching when it is not.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.anomaly.baseline import BASELINE_METRICS
from app.ips.inputs import (
    IPS_BLOCK_RATIO_METRIC,
    IPS_ENTITY_EVENTS_METRIC,
    IPS_FANOUT_METRIC,
    IPS_TOP_SOURCE_SHARE_METRIC,
    IpsInput,
    IpsObservation,
)
from app.ips.inputs import input_measurement as ips_input_measurement
from app.models.enums import (
    AnomalyType,
    IpsAnomalyType,
    WafAnomalyType,
    WafBreakdown,
    WafMeasurement,
)
from app.providers.base import LogSourceTelemetry as T
from app.waf.baseline import (
    BLOCK_RATIO_METRIC,
    CLIENT_ERROR_RATIO_METRIC,
    SERVER_ERROR_RATIO_METRIC,
    TOP_CLIENT_SHARE_METRIC,
    WAF_BASELINE_METRIC,
)
from app.waf.inputs import HistoryFacts, WafInput, input_measurement
from app.waf.measurement import (
    ScopeDecision,
    WafMeasurementRequirement,
    narrowest,
    verdict_scope,
)
from app.waf.observations import EndpointObservation


class BehaviorDomain(StrEnum):
    LOG_SOURCE = "LOG_SOURCE"
    WAF_ENDPOINT = "WAF_ENDPOINT"
    IPS_ENTITY = "IPS_ENTITY"
    IPS_SOURCE = "IPS_SOURCE"


class DetectorStatus(StrEnum):
    #: Live Ariel collection supplies every input and a scheduled task runs it.
    OPERATIONAL = "OPERATIONAL"
    #: Runnable on live data, but no scheduled task executes it.
    NOT_SCHEDULED = "NOT_SCHEDULED"
    #: Scheduled, but the telemetry it reads is opt-in and switched off in this
    #: deployment's settings. Reported only when settings are supplied.
    NOT_ENABLED = "NOT_ENABLED"
    #: Only the mock provider supplies its inputs. Never runs on live data.
    SYNTHETIC_ONLY = "SYNTHETIC_ONLY"
    #: No collection path supplies its inputs, synthetic or live.
    UNSUPPORTED = "UNSUPPORTED"


#: Every baseline metric the WAF builder trains.
WAF_BASELINE_METRICS = frozenset(
    {
        WAF_BASELINE_METRIC,
        BLOCK_RATIO_METRIC,
        SERVER_ERROR_RATIO_METRIC,
        CLIENT_ERROR_RATIO_METRIC,
        TOP_CLIENT_SHARE_METRIC,
    }
)


#: Every baseline metric the IPS baseline builder will train (design D.7).
IPS_BASELINE_METRICS = frozenset(
    {
        IPS_ENTITY_EVENTS_METRIC,
        IPS_TOP_SOURCE_SHARE_METRIC,
        IPS_FANOUT_METRIC,
        IPS_BLOCK_RATIO_METRIC,
    }
)


@dataclass(frozen=True)
class InputRequirement:
    """What one measured input of a WAF or IPS detector must be for a verdict.

    Declared per input because the mathematics differs per input: a ratio's
    numerator may prove an increase as a lower bound, its denominator may not.
    """

    input: WafInput | IpsInput
    #: The input's role, e.g. "numerator" or "denominator".
    role: str
    requirement: WafMeasurementRequirement
    #: Why this requirement is the right one, stated for operators.
    rationale: str


@dataclass(frozen=True)
class DetectorSpec:
    domain: BehaviorDomain
    detector_type: str
    #: Per-bucket log-source telemetry the detector reads.
    telemetry: frozenset[T]
    #: Baseline metric names the detector compares against.
    baseline_metrics: frozenset[str]
    #: Celery task that executes it in production, or None.
    scheduled_task: str | None
    #: What stops it running on live data. None when nothing does.
    limitation: str | None = None
    #: WAF/IPS: how the detector may use a count that is a lower bound
    #: (grouping truncated, or coverage below its floor). None for log-source
    #: detectors, whose bucket volume is one ungrouped aggregate.
    measurement: WafMeasurementRequirement | None = None
    #: WAF/IPS: the requirement on each measured input. A verdict is only as
    #: strong as the narrowest of them.
    inputs: tuple[InputRequirement, ...] = ()
    #: WAF only: optional breakdown searches the detector reads.
    breakdowns: frozenset[WafBreakdown] = frozenset()
    #: WAF only: the setting that must be true before those breakdowns are
    #: collected. None when the detector needs no opt-in telemetry.
    enabling_setting: str | None = None
    #: What must be configured, beyond the setting, before it can reach a
    #: verdict on a source. Informational; not a limitation of the code.
    activation: str | None = None
    #: False for a named detector with no implementation.
    implemented: bool = True


#: What rows from each collection path measure, for rows written before the
#: collector recorded `telemetry` in provenance. Guarded against drift from the
#: provider declarations by a unit test.
ARIEL_AGGREGATE_TELEMETRY = frozenset({T.EVENT_VOLUME, T.LAST_EVENT_TIME})
SYNTHETIC_TELEMETRY = frozenset(T) - {T.COLLECTION_ERRORS}
_TELEMETRY_BY_COLLECTION_SOURCE: dict[str, frozenset[T]] = {
    "ariel_aggregate": ARIEL_AGGREGATE_TELEMETRY,
    "mock": SYNTHETIC_TELEMETRY,
}
#: Every bucket row carries a volume by construction; anything more must be
#: declared by provenance or known from the collection path.
_ALWAYS_MEASURED = frozenset({T.EVENT_VOLUME})

_PHASE_A_TASK = "evaluate_anomalies"


def _log_source(
    anomaly_type: AnomalyType,
    telemetry: set[T],
    baselines: set[str] | None = None,
    limitation: str | None = None,
) -> DetectorSpec:
    return DetectorSpec(
        domain=BehaviorDomain.LOG_SOURCE,
        detector_type=anomaly_type.value,
        telemetry=frozenset(telemetry),
        baseline_metrics=frozenset(baselines or ()),
        scheduled_task=_PHASE_A_TASK,
        limitation=limitation,
    )


LOG_SOURCE_DETECTORS: dict[AnomalyType, DetectorSpec] = {
    AnomalyType.NO_EVENTS: _log_source(
        AnomalyType.NO_EVENTS, {T.EVENT_VOLUME}, {"event_count"}
    ),
    AnomalyType.VOLUME_DROP: _log_source(
        AnomalyType.VOLUME_DROP, {T.EVENT_VOLUME}, {"average_eps"}
    ),
    AnomalyType.VOLUME_SPIKE: _log_source(
        AnomalyType.VOLUME_SPIKE, {T.EVENT_VOLUME}, {"average_eps"}
    ),
    AnomalyType.PARSING_DEGRADATION: _log_source(
        AnomalyType.PARSING_DEGRADATION,
        {T.PARSE_RATIOS},
        limitation=(
            "The Ariel metric aggregate does not compute field-extraction ratios; "
            "only the mock provider synthesizes them."
        ),
    ),
    AnomalyType.UNKNOWN_EVENT_SPIKE: _log_source(
        AnomalyType.UNKNOWN_EVENT_SPIKE,
        {T.EVENT_VOLUME, T.UNKNOWN_EVENT_COUNT},
        limitation=(
            "The Ariel metric aggregate does not count unclassified events; "
            "only the mock provider synthesizes them."
        ),
    ),
    AnomalyType.TIMESTAMP_DELAY: _log_source(
        AnomalyType.TIMESTAMP_DELAY,
        {T.INGESTION_DELAY},
        limitation=(
            "The Ariel metric aggregate records only the gap from the newest event "
            "to the bucket end, which the bucket width bounds and which is not "
            "ingestion delay. Measuring delay needs device time against receive "
            "time; only the mock provider synthesizes it."
        ),
    ),
    AnomalyType.CARDINALITY_DROP: _log_source(
        AnomalyType.CARDINALITY_DROP,
        {T.DISTINCT_SOURCE_IPS},
        {"distinct_source_ip_count"},
        limitation=(
            "No distinct-source-IP baseline is built, and the Ariel metric "
            "aggregate does not count distinct source IPs."
        ),
    ),
    AnomalyType.COLLECTION_ERROR: _log_source(
        AnomalyType.COLLECTION_ERROR,
        {T.COLLECTION_ERRORS},
        limitation=(
            "A failed collection raises before any bucket is written, so no "
            "bucket can carry an error count. Collection failures are recorded on "
            "the collection watermark instead."
        ),
    ),
    AnomalyType.REPEATED_PAYLOAD: _log_source(
        AnomalyType.REPEATED_PAYLOAD,
        {T.PAYLOAD_SIGNATURE},
        limitation=(
            "A payload signature requires reading event payloads, which the "
            "platform never retrieves; only the mock provider synthesizes one."
        ),
    ),
}

_EXACT = WafMeasurementRequirement.EXACT
_INCREASE = WafMeasurementRequirement.LOWER_BOUND_PROVES_INCREASE
_WAF_TASK = "dispatch_waf_collection"
_COVERAGE = InputRequirement(
    WafInput.REQUEST_COUNT,
    "population coverage",
    _EXACT,
    "the breakdown field's coverage of the endpoint's requests is checked against the "
    "exact request count; a lower bound cannot verify that coverage meets its floor",
)


def _ratio_inputs(numerator: WafInput, denominator: WafInput, uncertain: str) -> tuple[
    InputRequirement, ...
]:
    return (
        InputRequirement(
            numerator,
            "numerator",
            _INCREASE,
            f"{uncertain}; at a fixed exact denominator the proportion and every guard are "
            "non-decreasing in the numerator, so a lower bound that fires proves the rise",
        ),
        InputRequirement(
            denominator,
            "denominator",
            _EXACT,
            "a lower-bound denominator can make a proportion too large or too small, so a "
            "ratio over one bounds nothing",
        ),
        _COVERAGE,
    )


def _waf(
    detector_type: WafAnomalyType,
    *,
    baselines: set[str],
    inputs: tuple[InputRequirement, ...],
    breakdowns: set[WafBreakdown] | None = None,
    enabling_setting: str | None = None,
    activation: str | None = None,
    measurement: WafMeasurementRequirement | None = None,
) -> DetectorSpec:
    return DetectorSpec(
        domain=BehaviorDomain.WAF_ENDPOINT,
        detector_type=detector_type.value,
        telemetry=frozenset(),
        baseline_metrics=frozenset(baselines),
        # Beat dispatches one `collect_waf_source` run per eligible source, which
        # collects, then evaluates every registered WAF detector, then trains. It
        # runs only for sources with an enabled, validated profile, and only
        # while WAF_COLLECTION_ENABLED is true.
        scheduled_task=_WAF_TASK,
        measurement=measurement,
        inputs=inputs,
        breakdowns=frozenset(breakdowns or ()),
        enabling_setting=enabling_setting,
        activation=activation,
    )


WAF_DETECTORS: dict[WafAnomalyType, DetectorSpec] = {
    WafAnomalyType.WAF_REQUEST_RATE_SPIKE: _waf(
        WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
        baselines={WAF_BASELINE_METRIC},
        # Every guard on the way to a spike verdict is non-decreasing in the
        # request count, so a lower bound that fires proves the spike. A normal
        # verdict still needs an exact count.
        measurement=_INCREASE,
        inputs=(
            InputRequirement(
                WafInput.REQUEST_COUNT,
                "volume",
                _INCREASE,
                "minimum volume, ratio, absolute delta, robust z and the degenerate-MAD band "
                "are all non-decreasing in the request count",
            ),
        ),
    ),
    WafAnomalyType.WAF_BLOCK_RATIO_SPIKE: _waf(
        WafAnomalyType.WAF_BLOCK_RATIO_SPIKE,
        baselines={BLOCK_RATIO_METRIC},
        inputs=_ratio_inputs(
            WafInput.BLOCKED_REQUESTS,
            WafInput.ACTION_EVENTS,
            "an action value the map does not name may have been a block, so blocked "
            "requests are a lower bound while any exist",
        ),
        breakdowns={WafBreakdown.ACTION},
        enabling_setting="waf_outcome_collection_enabled",
        activation=(
            "the profile maps an action property that validated AVAILABLE, and authors an "
            "action map naming every discovered value with at least one BLOCKED value"
        ),
    ),
    WafAnomalyType.WAF_ERROR_RATIO_SPIKE: _waf(
        WafAnomalyType.WAF_ERROR_RATIO_SPIKE,
        baselines={SERVER_ERROR_RATIO_METRIC},
        inputs=_ratio_inputs(
            WafInput.SERVER_ERRORS,
            WafInput.RESPONSE_CODE_EVENTS,
            "a response-code value that is not a three-digit status may have been a 5xx, "
            "so 5xx responses are a lower bound while any exist",
        ),
        breakdowns={WafBreakdown.RESPONSE_CODE},
        enabling_setting="waf_outcome_collection_enabled",
        activation="the profile maps a response-code property that validated AVAILABLE",
    ),
    WafAnomalyType.WAF_CLIENT_ERROR_RATIO_SPIKE: _waf(
        WafAnomalyType.WAF_CLIENT_ERROR_RATIO_SPIKE,
        baselines={CLIENT_ERROR_RATIO_METRIC},
        inputs=_ratio_inputs(
            WafInput.CLIENT_ERRORS,
            WafInput.RESPONSE_CODE_EVENTS,
            "a response-code value that is not a three-digit status may have been a 4xx, "
            "so 4xx responses are a lower bound while any exist",
        ),
        breakdowns={WafBreakdown.RESPONSE_CODE},
        enabling_setting="waf_outcome_collection_enabled",
        activation="the profile maps a response-code property that validated AVAILABLE",
    ),
    WafAnomalyType.WAF_CLIENT_CONCENTRATION_SPIKE: _waf(
        WafAnomalyType.WAF_CLIENT_CONCENTRATION_SPIKE,
        baselines={TOP_CLIENT_SHARE_METRIC},
        inputs=(
            InputRequirement(
                WafInput.TOP_CLIENT_REQUESTS,
                "largest contributor",
                _EXACT,
                "in a truncated breakdown the largest source IP may be hidden entirely, and "
                "hidden raw groups can fold into a visible one, so no bound on it exists",
            ),
            InputRequirement(
                WafInput.CLIENT_EVENTS,
                "total",
                _EXACT,
                "a share over a lower-bound total bounds nothing",
            ),
            _COVERAGE,
        ),
        breakdowns={WafBreakdown.SOURCE_IP},
        enabling_setting="waf_client_collection_enabled",
        activation=(
            "none beyond the setting; the source IP is QRadar's normalized sourceip, which "
            "behind a proxy, load balancer or NAT is the intermediary, not the end user"
        ),
    ),
    WafAnomalyType.WAF_NEW_ENDPOINT_ACCESS: _waf(
        WafAnomalyType.WAF_NEW_ENDPOINT_ACCESS,
        baselines=set(),
        inputs=(
            InputRequirement(
                WafInput.ENDPOINT_HISTORY,
                "history",
                _EXACT,
                "'not seen before' is an absence claim: every bucket of the history window "
                "must be recorded, with whole discovery and whole admission, under the same "
                "profile and normalizer",
            ),
            InputRequirement(
                WafInput.REQUEST_COUNT,
                "materiality",
                _INCREASE,
                "minimum requests and buckets with traffic are non-decreasing in the counts",
            ),
        ),
    ),
    WafAnomalyType.WAF_CLIENT_ENDPOINT_FANOUT_SPIKE: DetectorSpec(
        domain=BehaviorDomain.WAF_ENDPOINT,
        detector_type=WafAnomalyType.WAF_CLIENT_ENDPOINT_FANOUT_SPIKE.value,
        telemetry=frozenset(),
        baseline_metrics=frozenset(),
        scheduled_task=None,
        implemented=False,
        limitation=(
            "Distinct normalized endpoints per source IP need a search grouped by raw URL, "
            "method, host and source IP, because normalization cannot run in AQL. Under its "
            "group cap that search drops the lowest-volume groups first -- one request per "
            "path, exactly the enumeration signature -- so the lower bound it leaves is "
            "structurally blind to the behaviour. A per-client baseline would be an "
            "unbounded per-client entity, and no source-scoped incident subject exists. "
            "Needs a QRadar-side normalized-endpoint property (so AQL can count distinct "
            "endpoints per source IP with client-bounded cardinality) with verified "
            "UNIQUECOUNT exactness, and a source-scoped detector subject."
        ),
    ),
}


def waf_input_scope(
    detector_type: WafAnomalyType,
    obs: EndpointObservation,
    history: HistoryFacts | None = None,
) -> ScopeDecision:
    """What a WAF detector may conclude, from each declared input's measurement.

    Falls back to the single `measurement` requirement over the request count for
    a spec that declares no inputs. The narrowest input decides.
    """
    spec = WAF_DETECTORS[detector_type]
    if not spec.inputs:
        return waf_verdict_scope(detector_type, obs.measurement)
    decisions: list[ScopeDecision] = []
    for req in spec.inputs:
        assert isinstance(req.input, WafInput), req.input
        decision = verdict_scope(req.requirement, input_measurement(req.input, obs, history))
        if decision.reason is not None:
            decision = ScopeDecision(decision.scope, f"{req.role}: {decision.reason}")
        decisions.append(decision)
    return narrowest(decisions)


_IPS_ENTITY_DOMAINS = {
    IpsAnomalyType.IPS_PORT_RATE_SPIKE,
    IpsAnomalyType.IPS_DESTINATION_RATE_SPIKE,
    IpsAnomalyType.IPS_SIGNATURE_BURST,
}
_IPS_TASK = "dispatch_ips_collection"


def _ips(
    detector_type: IpsAnomalyType,
    *,
    baselines: set[str],
    inputs: tuple[InputRequirement, ...],
    activation: str | None = None,
    measurement: WafMeasurementRequirement | None = None,
) -> DetectorSpec:
    return DetectorSpec(
        domain=(
            BehaviorDomain.IPS_ENTITY
            if detector_type in _IPS_ENTITY_DOMAINS
            else BehaviorDomain.IPS_SOURCE
        ),
        detector_type=detector_type.value,
        telemetry=frozenset(),
        baseline_metrics=frozenset(baselines),
        # Fleet collection evaluates every source before bounded baseline training.
        scheduled_task=_IPS_TASK,
        enabling_setting="ips_collection_enabled",
        measurement=measurement,
        inputs=inputs,
        activation=activation,
    )


def _ips_entity_inputs() -> tuple[InputRequirement, ...]:
    return (
        InputRequirement(
            IpsInput.ENTITY_EVENTS,
            "volume",
            _INCREASE,
            "minimum events, ratio, absolute delta, robust z and the degenerate-MAD band are "
            "all non-decreasing in the entity's event count",
        ),
        InputRequirement(
            IpsInput.DIMENSION_EVENTS,
            "coverage",
            _INCREASE,
            "coverage only has to reach its floor, and a lower bound on coverage that reaches "
            "it proves the true coverage does",
        ),
    )


IPS_DETECTORS: dict[IpsAnomalyType, DetectorSpec] = {
    IpsAnomalyType.IPS_PORT_RATE_SPIKE: _ips(
        IpsAnomalyType.IPS_PORT_RATE_SPIKE,
        baselines={IPS_ENTITY_EVENTS_METRIC},
        inputs=_ips_entity_inputs(),
        measurement=_INCREASE,
    ),
    IpsAnomalyType.IPS_DESTINATION_RATE_SPIKE: _ips(
        IpsAnomalyType.IPS_DESTINATION_RATE_SPIKE,
        baselines={IPS_ENTITY_EVENTS_METRIC},
        inputs=_ips_entity_inputs(),
        measurement=_INCREASE,
    ),
    IpsAnomalyType.IPS_SIGNATURE_BURST: _ips(
        IpsAnomalyType.IPS_SIGNATURE_BURST,
        baselines={IPS_ENTITY_EVENTS_METRIC},
        inputs=_ips_entity_inputs(),
        measurement=_INCREASE,
        activation="the signature has a specific QID name; 'Unknown ...' names are refused",
    ),
    IpsAnomalyType.IPS_NEW_DESTINATION_PORT: _ips(
        IpsAnomalyType.IPS_NEW_DESTINATION_PORT,
        baselines=set(),
        inputs=(
            InputRequirement(
                IpsInput.PORT_HISTORY,
                "history",
                _EXACT,
                "'below the admission threshold throughout the history' is an absence claim: "
                "every history bucket must be recorded, with an exact port search, coverage at "
                "its floor, whole admission, the same profile and normalizer, and no maintenance",
            ),
            InputRequirement(
                IpsInput.ENTITY_EVENTS,
                "materiality",
                _INCREASE,
                "minimum events and buckets with traffic are non-decreasing in the counts",
            ),
        ),
    ),
    IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE: _ips(
        IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
        baselines={IPS_TOP_SOURCE_SHARE_METRIC},
        inputs=(
            InputRequirement(
                IpsInput.SOURCE_IP_TOP1_EVENTS,
                "largest contributor",
                _EXACT,
                "in a truncated source-IP search the largest address may be hidden entirely, so "
                "no bound on its events exists",
            ),
            InputRequirement(
                IpsInput.SOURCE_IP_EVENTS,
                "total",
                _EXACT,
                "a share over a lower-bound total bounds nothing",
            ),
        ),
    ),
    IpsAnomalyType.IPS_PORT_FANOUT_SPIKE: _ips(
        IpsAnomalyType.IPS_PORT_FANOUT_SPIKE,
        baselines={IPS_FANOUT_METRIC},
        measurement=_INCREASE,
        inputs=(
            InputRequirement(
                IpsInput.MAX_DISTINCT_PORTS,
                "largest fan-out",
                _INCREASE,
                "the largest distinct-port count of one source IP is non-decreasing in what "
                "was seen, and every guard is non-decreasing in it, so a lower bound that "
                "fires proves the true maximum does",
            ),
            InputRequirement(
                IpsInput.DESTINATION_PORT_EVENTS,
                "destination-port coverage",
                _INCREASE,
                "fan-out excludes events without a usable destination port; an exact or "
                "lower-bound destination-port population at the coverage floor is required",
            ),
        ),
    ),
    IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT: _ips(
        IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT,
        baselines={IPS_BLOCK_RATIO_METRIC},
        inputs=(
            InputRequirement(
                IpsInput.BLOCKED_EVENTS,
                "numerator",
                _INCREASE,
                "an action value the map does not name may have been a block, so blocked "
                "events are a lower bound while any exist; at a fixed exact denominator the "
                "proportion and every guard are non-decreasing in the numerator",
            ),
            InputRequirement(
                IpsInput.ACTION_EVENTS,
                "denominator",
                _EXACT,
                "a lower-bound denominator can make a proportion too large or too small, so a "
                "ratio over one bounds nothing",
            ),
        ),
        activation=(
            "the source's IPS profile maps an ACTION field and authors an action value map "
            "naming at least one BLOCKED value"
        ),
    ),
}


def ips_input_scope(
    detector_type: IpsAnomalyType,
    obs: IpsObservation,
    history: HistoryFacts | None = None,
) -> ScopeDecision:
    """What an IPS detector may conclude, from each declared input's measurement.

    The narrowest declared input decides. A spec that declares no inputs falls
    back to its single `measurement` requirement over the entity count.
    """
    spec = IPS_DETECTORS[detector_type]
    if not spec.inputs:
        return verdict_scope(
            spec.measurement or WafMeasurementRequirement.EXACT,
            ips_input_measurement(IpsInput.ENTITY_EVENTS, obs, history),
        )
    decisions: list[ScopeDecision] = []
    for req in spec.inputs:
        assert isinstance(req.input, IpsInput), req.input
        decision = verdict_scope(req.requirement, ips_input_measurement(req.input, obs, history))
        if decision.reason is not None:
            decision = ScopeDecision(decision.scope, f"{req.role}: {decision.reason}")
        decisions.append(decision)
    return narrowest(decisions)


def bucket_telemetry(
    collection_source: str | None, query_provenance: Mapping[str, Any] | None
) -> frozenset[T]:
    """What one stored log-source bucket actually measured.

    The collector's own declaration wins. Rows written before it recorded one
    fall back to what their collection path is known to measure, and an unknown
    path is credited with volume alone.
    """
    declared = (query_provenance or {}).get("telemetry")
    if isinstance(declared, list):
        known = set(T)
        return frozenset(T(v) for v in declared if v in known) | _ALWAYS_MEASURED
    return _TELEMETRY_BY_COLLECTION_SOURCE.get(collection_source or "", _ALWAYS_MEASURED)


def unmet_requirement(anomaly_type: AnomalyType, telemetry: frozenset[T]) -> str | None:
    """Why this detector cannot judge a bucket with `telemetry`; None if it can."""
    spec = LOG_SOURCE_DETECTORS[anomaly_type]
    missing_baselines = spec.baseline_metrics - frozenset(BASELINE_METRICS)
    if missing_baselines:
        return f"no {', '.join(sorted(missing_baselines))} baseline is built"
    missing = spec.telemetry - telemetry
    if missing:
        return f"bucket did not measure {', '.join(sorted(missing))}"
    return None


def waf_verdict_scope(
    detector_type: WafAnomalyType, measurement: WafMeasurement | None
) -> ScopeDecision:
    """What a WAF detector may conclude from a count with this measurement.

    A detector without a declared requirement is treated as needing exact
    counts: lower-bound reasoning must be claimed explicitly, never inherited.
    """
    spec = WAF_DETECTORS[detector_type]
    return verdict_scope(spec.measurement or WafMeasurementRequirement.EXACT, measurement)


def detector_status(spec: DetectorSpec, settings: Any | None = None) -> DetectorStatus:
    """What the code supports, and -- given `settings` -- what this deployment runs."""
    if spec.domain in (BehaviorDomain.IPS_ENTITY, BehaviorDomain.IPS_SOURCE):
        if not spec.implemented or not spec.baseline_metrics <= IPS_BASELINE_METRICS:
            return DetectorStatus.UNSUPPORTED
        if not spec.scheduled_task:
            return DetectorStatus.NOT_SCHEDULED
        if (
            settings is not None
            and spec.enabling_setting is not None
            and not getattr(settings, spec.enabling_setting)
        ):
            return DetectorStatus.NOT_ENABLED
        return DetectorStatus.OPERATIONAL
    if spec.domain is BehaviorDomain.WAF_ENDPOINT:
        if not spec.implemented or not spec.baseline_metrics <= WAF_BASELINE_METRICS:
            return DetectorStatus.UNSUPPORTED
        if not spec.scheduled_task:
            return DetectorStatus.NOT_SCHEDULED
        if (
            settings is not None
            and spec.enabling_setting is not None
            and not getattr(settings, spec.enabling_setting)
        ):
            return DetectorStatus.NOT_ENABLED
        return DetectorStatus.OPERATIONAL
    if not spec.baseline_metrics <= frozenset(BASELINE_METRICS):
        return DetectorStatus.UNSUPPORTED
    if spec.telemetry <= ARIEL_AGGREGATE_TELEMETRY:
        return (
            DetectorStatus.OPERATIONAL if spec.scheduled_task else DetectorStatus.NOT_SCHEDULED
        )
    if spec.telemetry <= SYNTHETIC_TELEMETRY:
        return DetectorStatus.SYNTHETIC_ONLY
    return DetectorStatus.UNSUPPORTED


def all_detectors() -> list[DetectorSpec]:
    return [*LOG_SOURCE_DETECTORS.values(), *WAF_DETECTORS.values(), *IPS_DETECTORS.values()]
