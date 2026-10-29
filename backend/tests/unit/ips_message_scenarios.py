"""The IPS detector scenarios behind the MSG-6b sentence table.

One scenario per sentence shape (and per variant of a shape). Each is a name and a thunk that
returns ``(signal, English reason)``; `test_ips_messages.py` pins the English each one produced
before the sentences had codes. The module only builds contexts, so the same scenarios run
against the code at the base commit (where the dump script of the task captured the English
table) and against the current code.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from app.ips.behavior import (
    appearance_flood,
    detect_block_ratio_shift,
    detect_new_destination_port,
    detect_port_fanout_spike,
    detect_source_concentration_spike,
    verify_port_history,
)
from app.ips.detector import (
    IpsDetectionContext,
    IpsVerdict,
    detect_destination_rate_spike,
    detect_port_rate_spike,
    detect_signature_burst,
)
from app.ips.inputs import EntityCount, IpsBucketMeasurement, IpsObservation
from app.models.enums import IpsAnomalyType, IpsDimension, WafMeasurement
from app.waf.inputs import HistoryFacts
from tests.unit import test_ips_detectors as ti

Scenario = Callable[[], tuple[str, str]]

SCENARIOS: dict[str, Scenario] = {}
#: The same scenarios, un-flattened: the verdict (or the history facts) itself.
RAW: dict[str, Callable[[], object]] = {}

_dim, _bkt, _cel = ti._dimension, ti._bucket, ti._cell

PORT = IpsDimension.DESTINATION_PORT
DEST = IpsDimension.DESTINATION_IP
SIG = IpsDimension.SIGNATURE
CONC = IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE
BLOCK = IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT
FANOUT = IpsAnomalyType.IPS_PORT_FANOUT_SPIKE
LOWER = IpsBucketMeasurement.LOWER_BOUND
FIELD = {PORT: "destination_port", DEST: "destination_ip", SIG: "signature"}
DETECTORS = {
    PORT: detect_port_rate_spike,
    DEST: detect_destination_rate_spike,
    SIG: detect_signature_burst,
}
BIG = {"median": 1000.0, "mad": 200.0, "p05": 800.0, "p95": 1500.0}
ZERO = {"median": 0.0, "mad": 0.0, "p05": 0.0, "p95": 10.0}
UNSET = {"coverage": None, "admission_truncated": None}


def _add(name: str, fn: Callable[[], IpsVerdict | tuple[str, str]]) -> None:
    assert name not in SCENARIOS, name
    RAW[name] = fn

    def run() -> tuple[str, str]:
        out = fn()
        if isinstance(out, tuple):
            return out
        return out.signal.value, out.evidence.reason

    SCENARIOS[name] = run


def _spike(dimension: IpsDimension, **kw: object) -> Callable[[], IpsVerdict]:
    return lambda: DETECTORS[dimension](ti._entity_context(dimension, **kw))  # type: ignore[arg-type]


def _lower_bucket(dimension: IpsDimension, **changes: object):
    return _bkt(**{FIELD[dimension]: _dim(LOWER, coverage=0.95)}, **changes)


def _lower(dimension: IpsDimension, events: int = 100, **kw: object):
    return _spike(
        dimension, events=events, measurement=WafMeasurement.LOWER_BOUND,
        bucket=_lower_bucket(dimension), **kw,
    )


# ---------------------------------------------------------------- entity spikes
for _d in (PORT, DEST, SIG):
    _n = _d.value.lower()
    _add(f"spike.{_n}.fired", _spike(_d))
    _add(f"spike.{_n}.lower_fired", _lower(_d))
_add("spike.fired_big", _spike(PORT, events=12345, baseline=_cel(**BIG)))
_add("spike.fired_mad_above_p95", _spike(PORT, baseline=_cel(mad=0.0, p95=15.0)))
_add("spike.fired_expected_zero", _spike(PORT, baseline=_cel(**ZERO)))
_add("spike.lower_fired_big", _lower(PORT, events=12345, baseline=_cel(**BIG)))
_add("spike.lower_fired_mad_above_p95", _lower(PORT, baseline=_cel(mad=0.0, p95=15.0)))
_add("spike.lower_fired_expected_zero", _lower(PORT, baseline=_cel(**ZERO)))
_add("spike.below_min", _spike(PORT, events=15))
_add("spike.ratio_not_reached", _spike(PORT, events=50, baseline=_cel(median=40.0)))
_add("spike.delta_below_min", _spike(PORT, events=25))
_add("spike.z_not_reached", _spike(PORT, events=70, baseline=_cel(mad=50.0)))
_add("spike.mad_within_p95", _spike(PORT, events=70, baseline=_cel(mad=0.0, p95=1000.0)))
_add("spike.below_min_big", _spike(PORT, events=15, baseline=_cel(median=5000.0)))
_add("spike.lower_below_min", _lower(PORT, events=15))
_add("spike.lower_ratio_not_reached", _lower(PORT, events=50, baseline=_cel(median=40.0)))
_add("spike.lower_delta_below_min", _lower(PORT, events=25))
_add("spike.lower_z_not_reached", _lower(PORT, events=70, baseline=_cel(mad=50.0)))
_add("spike.lower_mad_within_p95", _lower(PORT, events=70, baseline=_cel(mad=0.0, p95=1000.0)))
_add("spike.maintenance", _spike(PORT, bucket=_bkt(in_maintenance=True)))
_add(
    "spike.no_entity",
    lambda: detect_port_rate_spike(
        IpsDetectionContext(observation=IpsObservation(bucket=_bkt()), baseline=_cel())
    ),
)
_add("spike.wrong_dimension", lambda: detect_port_rate_spike(ti._entity_context(DEST)))
for _value in ("0/tcp", "443/255"):
    _add(
        f"spike.absent_port.{_value}",
        _spike(PORT, entity=ti._entity(PORT, value_key=_value)),
    )
_add(
    "spike.generic_signature",
    _spike(SIG, entity=ti._entity(SIG, display_name="Unknown Event")),
)
_add("spike.count_unknown_text", _spike(PORT, events=None))
_add(
    "spike.count_unknown_default",
    lambda: detect_port_rate_spike(
        IpsDetectionContext(
            observation=IpsObservation(
                bucket=_bkt(), entity=ti._entity(PORT), count=EntityCount(None, None)
            ),
            baseline=_cel(),
        )
    ),
)
for _d in (PORT, DEST, SIG):
    _n = _d.value.lower()
    for _state in (
        IpsBucketMeasurement.NOT_COLLECTED,
        IpsBucketMeasurement.FAILED,
        IpsBucketMeasurement.REJECTED,
    ):
        _add(
            f"spike.{_n}.dimension_{_state.value.lower()}",
            _spike(
                _d,
                bucket=_bkt(
                    **{FIELD[_d]: _dim(_state, coverage=None, admission_truncated=None)}
                ),
            ),
        )
    _add(
        f"spike.{_n}.coverage_low",
        _spike(_d, bucket=_bkt(**{FIELD[_d]: _dim(coverage=0.89)})),
    )
_add(
    "spike.coverage_unmeasurable",
    _spike(PORT, bucket=_bkt(destination_port=_dim(coverage=None))),
)
_add("spike.scope_unstated", _spike(PORT, measurement=None))


def _no_baseline() -> IpsVerdict:
    ctx = ti._entity_context(PORT)
    ctx.baseline = None
    return detect_port_rate_spike(ctx)


_add("spike.no_baseline", _no_baseline)
_add("spike.baseline_unreliable", _spike(PORT, baseline=_cel(is_reliable=False)))
_add("spike.baseline_samples", _spike(PORT, baseline=_cel(sample_count=7)))
_add("spike.baseline_profile", _spike(PORT, baseline=_cel(profile_version=4)))
_add("spike.baseline_normalizer", _spike(PORT, baseline=_cel(normalizer_version=9)))
_add("spike.baseline_versions_none", _spike(PORT, baseline=_cel(profile_version=None)))

# --------------------------------------------------------------------- fan-out
_add("fanout.fired", lambda: detect_port_fanout_spike(ti._source_context(FANOUT)))
_add(
    "fanout.fired_big",
    lambda: detect_port_fanout_spike(
        ti._source_context(
            FANOUT,
            bucket=_bkt(max_distinct_ports=4321),
            baseline=_cel(median=1500.0, mad=100.0, p05=1000.0, p95=2000.0),
        )
    ),
)
_add(
    "fanout.lower_fired",
    lambda: detect_port_fanout_spike(
        ti._source_context(
            FANOUT, bucket=_bkt(port_fanout=_dim(LOWER, coverage=None))
        )
    ),
)
_add(
    "fanout.below_min",
    lambda: detect_port_fanout_spike(
        ti._source_context(FANOUT, bucket=_bkt(max_distinct_ports=15))
    ),
)
_add(
    "fanout.lower_below_min",
    lambda: detect_port_fanout_spike(
        ti._source_context(
            FANOUT,
            bucket=_bkt(
                max_distinct_ports=15, port_fanout=_dim(LOWER, coverage=None)
            ),
        )
    ),
)
_add(
    "fanout.unstated",
    lambda: detect_port_fanout_spike(
        ti._source_context(FANOUT, bucket=_bkt(max_distinct_ports=None))
    ),
)
_add(
    "fanout.port_coverage_low",
    lambda: detect_port_fanout_spike(
        ti._source_context(
            FANOUT, bucket=_bkt(destination_port=_dim(coverage=0.5))
        )
    ),
)
_add(
    "fanout.dimension_failed",
    lambda: detect_port_fanout_spike(
        ti._source_context(
            FANOUT,
            bucket=_bkt(
                port_fanout=_dim(
                    IpsBucketMeasurement.FAILED, coverage=None, admission_truncated=None
                )
            ),
        )
    ),
)
_add(
    "fanout.port_dimension_not_collected",
    lambda: detect_port_fanout_spike(
        ti._source_context(
            FANOUT,
            bucket=_bkt(
                destination_port=_dim(
                    IpsBucketMeasurement.NOT_COLLECTED, coverage=None, admission_truncated=None
                )
            ),
        )
    ),
)

# --------------------------------------------------------------- concentration
def _conc(**bucket: object) -> Callable[[], IpsVerdict]:
    return lambda: detect_source_concentration_spike(
        ti._source_context(CONC, bucket=_bkt(**bucket))
    )


_add("conc.fired", _conc())
_add("conc.fired_no_top5", _conc(top5_events=None))
_add("conc.top_below_min", _conc(top1_events=15))
_add("conc.total_below_min", _conc(top1_events=25, source_events=40, top5_events=30))
_add("conc.rise_below_min", _conc(top1_events=20, source_events=100, top5_events=30))
_add("conc.unstated", _conc(top1_events=None))
_add("conc.maintenance", _conc(in_maintenance=True))
_add(
    "conc.scope_needs_exact",
    _conc(source_ip=_dim(LOWER, coverage=0.95)),
)
_add(
    "conc.source_coverage_low",
    _conc(source_ip=_dim(coverage=0.5)),
)
_add(
    "conc.z_not_reached",
    lambda: detect_source_concentration_spike(
        ti._source_context(
            CONC,
            bucket=_bkt(top1_events=45, source_events=100),
            baseline=_cel("ratio", median=0.1, mad=0.2, numerator_sum=10.0, denominator_sum=100.0),
        )
    ),
)
_add(
    "conc.mad_within_p95",
    lambda: detect_source_concentration_spike(
        ti._source_context(
            CONC,
            bucket=_bkt(top1_events=45, source_events=100),
            baseline=_cel("ratio", mad=0.0, p95=0.9, numerator_sum=10.0, denominator_sum=100.0),
        )
    ),
)
_add(
    "conc.mad_above_p95",
    lambda: detect_source_concentration_spike(
        ti._source_context(CONC, baseline=_cel("ratio", mad=0.0, p95=0.15))
    ),
)
_add(
    "conc.no_denominator",
    lambda: detect_source_concentration_spike(
        ti._source_context(CONC, baseline=_cel("ratio", denominator_sum=0.0))
    ),
)

# ----------------------------------------------------------------- block ratio
def _block(baseline=None, **bucket: object) -> Callable[[], IpsVerdict]:
    return lambda: detect_block_ratio_shift(
        ti._source_context(BLOCK, bucket=_bkt(**bucket), baseline=baseline)
    )


_add("block.fired", _block())
NO_MULTIPLE = {"numerator_sum": 0.0, "median": 0.0, "mad": 0.0, "p05": 0.0, "p95": 0.0}
_add("block.fired_no_multiple", _block(baseline=_cel("ratio", **NO_MULTIPLE)))
_add("block.lower_fired", _block(unmapped_events=5, allowed_events=35))
_add(
    "block.lower_fired_no_multiple",
    _block(
        baseline=_cel("ratio", numerator_sum=0.0, median=0.0, mad=0.0, p05=0.0, p95=0.0),
        unmapped_events=5, allowed_events=35,
    ),
)
_add("block.numerator_below_min", _block(blocked_events=5, allowed_events=95))
_add(
    "block.lower_numerator_below_min",
    _block(blocked_events=5, allowed_events=90, unmapped_events=5),
)
_add("block.denominator_below_min", _block(blocked_events=10, allowed_events=30))
_add("block.rise_below_min", _block(blocked_events=15, allowed_events=85))
_add("block.lower_rise_below_min", _block(blocked_events=15, allowed_events=80, unmapped_events=5))
_add(
    "block.multiple_below_min",
    _block(
        baseline=_cel("ratio", numerator_sum=50.0, denominator_sum=100.0, median=0.5),
        blocked_events=62, allowed_events=38,
    ),
)
_add(
    "block.lower_multiple_below_min",
    _block(
        baseline=_cel("ratio", numerator_sum=50.0, denominator_sum=100.0, median=0.5),
        blocked_events=62, allowed_events=33, unmapped_events=5,
    ),
)
_add(
    "block.z_not_reached",
    _block(baseline=_cel("ratio", mad=0.3), blocked_events=30, allowed_events=70),
)
_add(
    "block.mad_within_p95",
    _block(baseline=_cel("ratio", mad=0.0, p95=0.9), blocked_events=30, allowed_events=70),
)
_add("block.mad_above_p95", _block(baseline=_cel("ratio", mad=0.0, p95=0.15)))
_add(
    "block.lower_z_not_reached",
    _block(
        baseline=_cel("ratio", mad=0.3), blocked_events=30, allowed_events=65,
        unmapped_events=5,
    ),
)
_add(
    "block.lower_mad_within_p95",
    _block(
        baseline=_cel("ratio", mad=0.0, p95=0.9), blocked_events=30, allowed_events=65,
        unmapped_events=5,
    ),
)
_add(
    "block.lower_mad_above_p95",
    _block(
        baseline=_cel("ratio", mad=0.0, p95=0.15), unmapped_events=5, allowed_events=35
    ),
)
_add(
    "block.no_action_mapped",
    _block(action=_dim(IpsBucketMeasurement.NOT_COLLECTED, **UNSET)),
)
_add("block.counts_unstated", _block(blocked_events=None))
_add("block.action_failed", _block(action=_dim(IpsBucketMeasurement.FAILED, **UNSET)))
_add("block.action_coverage_low", _block(action=_dim(coverage=0.5)))
_add("block.denominator_scope", _block(action=_dim(LOWER, coverage=0.95)))

# ------------------------------------------------------------------- new port
_NOW = ti.START


def _new(novelty=None, *, bucket=None, entity=None) -> Callable[[], IpsVerdict]:
    return lambda: detect_new_destination_port(
        ti._novelty_context(novelty=novelty, bucket=bucket, entity=entity)
    )


def _facts(**changes: object):
    return ti._novelty(**changes)


_add("port.fired", _new(_facts()))
_add("port.fired_big", _new(_facts(events_since_first_seen=12345, buckets_with_traffic=9)))
_add("port.fired_lower", _new(_facts(lower_bound=True)))
_add("port.below_materiality", _new(_facts(events_since_first_seen=5, buckets_with_traffic=1)))
_add(
    "port.below_materiality_lower",
    _new(_facts(events_since_first_seen=5, buckets_with_traffic=1, lower_bound=True)),
)
_add("port.not_established", _new(None))
_add("port.established_outside_window", _new(_facts(within_window=False)))
_add("port.readmitted", _new(_facts(readmitted=True)))
_add("port.maintenance", _new(_facts(), bucket=_bkt(in_maintenance=True)))
_add(
    "port.no_entity",
    lambda: detect_new_destination_port(
        IpsDetectionContext(observation=IpsObservation(bucket=_bkt()), novelty=_facts())
    ),
)
_add("port.wrong_dimension", _new(_facts(), entity=ti._entity(DEST)))
_add("port.absent_port", _new(_facts(), entity=ti._entity(PORT, value_key="0/tcp")))
_add(
    "port.dimension_failed",
    _new(
        _facts(),
        bucket=_bkt(
            destination_port=_dim(
                IpsBucketMeasurement.FAILED, coverage=None, admission_truncated=None
            )
        ),
    ),
)
_add(
    "port.coverage_low",
    _new(_facts(), bucket=_bkt(destination_port=_dim(coverage=0.5))),
)
_add("port.history_incomplete_default", _new(_facts(history=HistoryFacts(complete=False))))
_add(
    "port.history_incomplete_text",
    _new(_facts(history=HistoryFacts(complete=False, reason="a caller's own wording"))),
)
_add(
    "port.history_not_loaded",
    _new(
        _facts(
            history=HistoryFacts(
                complete=False, reason="history was not loaded for an established port"
            )
        )
    ),
)
_add(
    "port.history_never_collected",
    _new(
        _facts(
            history=HistoryFacts(
                complete=False, reason="the first-appearance bucket was never collected"
            )
        )
    ),
)
_add(
    "port.flood_discovery",
    _new(
        _facts(
            appearance_flood=appearance_flood(
                _bkt(destination_port=_dim(coverage=0.5)), min_coverage=0.9
            )
        )
    ),
)
_add(
    "port.flood_admission",
    _new(
        _facts(
            appearance_flood=appearance_flood(
                _bkt(destination_port=_dim(admission_truncated=True)),
                min_coverage=0.9,
            )
        )
    ),
)
_add(
    "port.flood_text",
    _new(_facts(appearance_flood="a caller's own wording")),
)


def _flood(**changes: object) -> tuple[str, str]:
    out = appearance_flood(_bkt(**changes), min_coverage=0.9)
    return ("FLOOD", out if out is not None else "none")


_add("flood.none", lambda: _flood())
_add("flood.not_measured", lambda: _flood(destination_port=_dim(coverage=0.5)))
_add(
    "flood.not_exact",
    lambda: _flood(destination_port=_dim(LOWER, coverage=0.95)),
)
_add("flood.admission", lambda: _flood(destination_port=_dim(admission_truncated=True)))
_add("flood.admission_unrecorded", lambda: _flood(destination_port=_dim(admission_truncated=None)))

# ------------------------------------------------------------ history verifier
#: The facts each history scenario just produced (the test reads the last one).
LAST_HISTORY: list[HistoryFacts] = []
_START = _NOW - timedelta(hours=3)


def _history(buckets=None, **kw: object) -> tuple[str, str]:
    rows = [
        _bkt(bucket_start=_START + timedelta(hours=i)) for i in range(3)
    ] if buckets is None else buckets
    args: dict[str, object] = dict(
        history_start=_START, history_end=_NOW, bucket_seconds=3600, profile_version=3,
        normalizer_version=2, min_coverage=0.9,
    )
    args.update(kw)
    facts = verify_port_history(rows, **args)  # type: ignore[arg-type]
    LAST_HISTORY.append(facts)
    return ("HISTORY", f"{facts.complete}: {facts.reason}")


def _rows(index: int = 1, **changes: object):
    return [
        _bkt(bucket_start=_START + timedelta(hours=i), **(changes if i == index else {}))
        for i in range(3)
    ]


_add("history.complete", lambda: _history())
_add("history.not_divisible", lambda: _history(bucket_seconds=7000))
_add("history.zero_width", lambda: _history(bucket_seconds=0))
_add("history.never_collected", lambda: _history(_rows()[:2]))
_add("history.record_count", lambda: _history(_rows() + _rows()[:1]))
_add("history.bucket_width", lambda: _history(_rows(bucket_seconds=1800)))
for _state in (
    IpsBucketMeasurement.NOT_COLLECTED,
    IpsBucketMeasurement.FAILED,
    IpsBucketMeasurement.REJECTED,
    LOWER,
):
    _add(
        f"history.port_search_{_state.value.lower()}",
        (lambda s: lambda: _history(
            _rows(destination_port=_dim(s, coverage=0.95))
        ))(_state),
    )
_add("history.coverage_low", lambda: _history(_rows(destination_port=_dim(coverage=0.5))))
_add(
    "history.admission_truncated",
    lambda: _history(_rows(destination_port=_dim(admission_truncated=True))),
)
_add(
    "history.admission_unrecorded",
    lambda: _history(_rows(destination_port=_dim(admission_truncated=None))),
)
_add("history.profile_version", lambda: _history(_rows(profile_version=2)))
_add("history.normalizer_version", lambda: _history(_rows(normalizer_version=1)))
_add("history.maintenance", lambda: _history(_rows(in_maintenance=True)))
