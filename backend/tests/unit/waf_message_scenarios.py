"""The WAF detector scenarios behind the MSG-6 sentence table.

One scenario per sentence shape (and per variant of a shape). Each is a name and a thunk that
returns the verdict; `test_waf_messages.py` pins the English each one produced before the
sentences had codes. The module only builds contexts, so the same scenarios run against the code
at the base commit (where the dump script of the task captured the English table) and against
the current code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

from app.models.enums import BucketCompleteness
from app.waf.baseline import WAF_BASELINE_METRIC
from app.waf.behavior import (
    detect_client_concentration_spike,
    detect_new_endpoint_access,
)
from app.waf.detector import WafSpikeVerdict, detect_request_rate_spike
from app.waf.inputs import HistoryFacts
from app.waf.measurement import WafVerdictScope
from app.waf.semantics import semantics_incompatibility
from tests.unit import test_waf_behavior as tb
from tests.unit import test_waf_detector as td

Scenario = Callable[[], WafSpikeVerdict]

#: The three sentences of the scope decision (`app.waf.measurement.verdict_scope`).
SCOPE_UNSTATED = "the bucket does not state whether its counts are exact or lower bounds"
SCOPE_INCREASE = "the count is a lower bound: an increase can be proven, normal behaviour cannot"
SCOPE_NEEDS_EXACT = "the detector needs exact counts and this count is a lower bound"

INC = WafVerdictScope.INCREASE_ONLY
NONE = WafVerdictScope.NONE
SCENARIOS: dict[str, Scenario] = {}


def scenario(name: str) -> Callable[[Scenario], Scenario]:
    def register(fn: Scenario) -> Scenario:
        assert name not in SCENARIOS, name
        SCENARIOS[name] = fn
        return fn

    return register


def _add(name: str, fn: Scenario) -> None:
    scenario(name)(fn)


# ------------------------------------------------------------------ request rate
def _rate(**overrides: object) -> Scenario:
    return lambda: detect_request_rate_spike(td.ctx(**overrides))


def _lower(
    requests: int = 500, scope_reason: str | None = SCOPE_INCREASE, **kw: object
) -> Scenario:
    return lambda: detect_request_rate_spike(
        td.ctx(
            point=td.point(requests=requests, completeness=BucketCompleteness.PARTIAL),
            verdict_scope=INC,
            scope_reason=scope_reason,
            **kw,
        )
    )


_add("rate.partial", _rate(point=td.point(completeness=BucketCompleteness.PARTIAL)))
_add("rate.missing", _rate(point=td.point(completeness=BucketCompleteness.MISSING)))
_add("rate.scope_unstated", _rate(verdict_scope=NONE, scope_reason=SCOPE_UNSTATED))
_add("rate.scope_needs_exact", _rate(verdict_scope=NONE, scope_reason=SCOPE_NEEDS_EXACT))
_add("rate.scope_plain_text", _rate(verdict_scope=NONE, scope_reason="a caller's own wording"))
_add("rate.coverage_low", _rate(point=td.point(coverage=0.62)))
_add(
    "rate.incompatible_bucket",
    _rate(baseline_incompatibility=semantics_incompatibility(None, "request_count/v1")),
)
_add(
    "rate.incompatible_baseline",
    _rate(baseline_incompatibility=semantics_incompatibility("request_count/v1", None)),
)
_add(
    "rate.incompatible_mismatch",
    _rate(
        baseline_incompatibility=semantics_incompatibility("request_count/v2", "request_count/v1")
    ),
)
_add("rate.no_baseline", _rate(baseline=None))
_add("rate.baseline_samples", _rate(baseline=td.cell(reliable=False, samples=3)))
_add("rate.below_min_volume", _rate(point=td.point(requests=12)))
_add("rate.ratio_not_reached", _rate(point=td.point(requests=150)))
_add("rate.delta_below_min", _rate(point=td.point(requests=45), baseline=td.cell(median=20.0)))
_add(
    "rate.z_not_reached",
    _rate(point=td.point(requests=250), baseline=td.cell(median=100.0, mad=40.0)),
)
_add(
    "rate.mad_within_band",
    _rate(
        point=td.point(requests=250),
        baseline=td.cell(median=100.0, mad=0.0, p05=80.0, p95=300.0),
    ),
)
_add("rate.fired", _rate())
_add("rate.fired_mad_outside_band", _rate(baseline=td.cell(mad=0.0)))
_add(
    "rate.fired_expected_zero",
    _rate(
        point=td.point(requests=300),
        baseline=td.cell(median=0.0, mad=0.0, p05=0.0, p95=10.0),
    ),
)
_add("rate.lower_fired", _lower())
_add("rate.lower_fired_plain_scope", _lower(scope_reason="a caller's own wording"))
_add("rate.lower_fired_no_scope", _lower(scope_reason=None))
_add("rate.lower_fired_mad_outside_band", _lower(baseline=td.cell(mad=0.0)))
_add("rate.lower_below_min_volume", _lower(requests=12))
_add("rate.lower_ratio_not_reached", _lower(requests=150))
_add("rate.lower_delta_below_min", _lower(requests=45, baseline=td.cell(median=20.0)))
_add("rate.lower_z_not_reached", _lower(requests=250, baseline=td.cell(median=100.0, mad=40.0)))
_add(
    "rate.lower_mad_within_band",
    _lower(requests=250, baseline=td.cell(median=100.0, mad=0.0, p05=80.0, p95=300.0)),
)


# ----------------------------------------------------------------------- ratios
def _ratio(kind: str, k: int, n: int = 100, **kw: object) -> Scenario:
    return lambda: tb.run(kind, tb.ratio_ctx(kind, k, n, **kw))


for _kind in tb.RATIO_IDS:
    p = f"ratio.{_kind}."
    _add(p + "not_collected", _ratio(_kind, 40, status=tb.NOT_COLLECTED))
    _add(p + "failed", _ratio(_kind, 40, status=tb.FAILED))
    _add(p + "scope_none_coded", _ratio(_kind, 40, scope=NONE, scope_reason=SCOPE_NEEDS_EXACT))
    _add(p + "scope_none_plain", _ratio(_kind, 40, scope=NONE, scope_reason="semantics unknown"))
    _add(p + "scope_none_no_reason", _ratio(_kind, 40, scope=NONE, scope_reason=None))
    _add(
        p + "source_coverage_low",
        lambda kind=_kind: tb.run(
            kind,
            _with_coverage(tb.ratio_ctx(kind, 40), 0.62),
        ),
    )
    _add(p + "field_coverage_low", _ratio(_kind, 40, 85, request_count=100))
    _add(
        p + "incompatible",
        _ratio(
            _kind, 40,
            baseline_incompatibility=semantics_incompatibility(
                "request_count/v2", "request_count/v1"
            ),
        ),
    )
    _add(p + "no_baseline", _ratio(_kind, 40, baseline=None))
    _add(p + "baseline_samples", _ratio(_kind, 40, baseline=tb.cell(reliable=False, samples=2)))
    _add(p + "no_denominator", _ratio(_kind, 40, baseline=tb.cell(denominator=0.0)))
    _add(p + "numerator_below_min", _ratio(_kind, 9, 100))
    _add(p + "denominator_below_min", _ratio(_kind, 20, 49))
    for label, k, n, base in tb.GUARD_CASES[2:]:
        _add(p + f"guard_{label}", _ratio(_kind, k, n, baseline=base))
        _add(
            p + f"lower_guard_{label}",
            _ratio(_kind, k, n, baseline=base, scope=INC, scope_reason=SCOPE_INCREASE),
        )
    _add(p + "lower_guard_no_scope", _ratio(_kind, 9, 100, scope=INC, scope_reason=None))
    _add(p + "lower_guard_plain_scope", _ratio(_kind, 9, 100, scope=INC, scope_reason="odd"))
    _add(p + "fired", _ratio(_kind, 40))
    _add(p + "fired_mad_above_p95", _ratio(_kind, 25, baseline=tb.cell(mad=0.0, p95=0.20)))
    zero = tb.cell(numerator=0, median=0.0, mad=0.0, p05=0.0, p95=0.0)
    _add(p + "fired_no_multiple", _ratio(_kind, 15, baseline=zero))
    _add(p + "lower_fired", _ratio(_kind, 40, scope=INC, scope_reason=SCOPE_INCREASE))
    _add(p + "lower_fired_no_multiple", _ratio(_kind, 15, baseline=zero, scope=INC))
    _add(
        p + "fired_no_entity",
        lambda kind=_kind: tb.run(kind, tb.ratio_ctx(kind, 40, extra={})),
    )


def _with_coverage(ctx: tb.WafDetectionContext, ratio: float) -> tb.WafDetectionContext:
    ctx.observation = replace(ctx.observation, endpoint_coverage_ratio=ratio)
    return ctx


# ---------------------------------------------------------------- concentration
def _conc(top1: int, n: int = 100, **kw: object) -> Scenario:
    return lambda: detect_client_concentration_spike(tb.conc_ctx(top1, n, **kw))


_add("conc.not_collected", _conc(60, status=tb.NOT_COLLECTED))
_add("conc.failed", _conc(60, status=tb.FAILED))
_add("conc.scope_inc_coded", _conc(60, scope=INC, scope_reason=SCOPE_INCREASE))
_add("conc.scope_plain", _conc(60, scope=NONE, scope_reason="needs exact counts"))
_add("conc.scope_no_reason", _conc(60, scope=NONE, scope_reason=None))
_add(
    "conc.source_coverage_low",
    lambda: detect_client_concentration_spike(_with_coverage(tb.conc_ctx(60), 0.62)),
)
_add("conc.attributed_low", _conc(60, 85, request_count=100))
_add("conc.no_baseline", _conc(60, base=None))
_add("conc.baseline_samples", _conc(60, base=tb.cell(reliable=False, samples=1)))
_add("conc.no_denominator", _conc(60, base=tb.cell(**{**tb.SHARE_CELL, "denominator": 0})))
_add("conc.top_below_min", _conc(19))
_add("conc.endpoint_below_min", _conc(25, 49))
_add("conc.rise_below_min", _conc(25, 100, base=tb.cell(**{**tb.SHARE_CELL, "numerator": 100})))
_add(
    "conc.z_not_reached",
    _conc(35, 100, base=tb.cell(**{**tb.SHARE_CELL, "mad": 0.10, "p95": 0.4})),
)
_add(
    "conc.mad_within_p95",
    _conc(35, 100, base=tb.cell(**{**tb.SHARE_CELL, "mad": 0.0, "p95": 0.4})),
)
_add("conc.fired_no_volume_baseline", _conc(60, top5=80))
_add(
    "conc.fired_volume_baseline",
    lambda: detect_client_concentration_spike(
        tb.conc_ctx(
            60, top5=80,
            baselines={WAF_BASELINE_METRIC: tb.cell(median=120.5, numerator=None)},
        )
    ),
)
_add(
    "conc.fired_mad_above_p95",
    _conc(60, base=tb.cell(**{**tb.SHARE_CELL, "mad": 0.0, "p95": 0.15})),
)
_add("conc.fired_no_entity", _conc(60, extra={}))


# ------------------------------------------------------------------ new endpoint
def _new(facts: object = "default", **kw: object) -> Scenario:
    return lambda: detect_new_endpoint_access(tb.new_ctx(facts, **kw))  # type: ignore[arg-type]


_add("new.no_facts", _new(None))
_add("new.outside_window", _new(tb.novelty(within_window=False)))
_add(
    "new.hand_off",
    _new(tb.novelty(within_window=False, history_end=tb.START + timedelta(days=1))),
)
_add(
    "new.history_reason",
    _new(
        tb.novelty(history=HistoryFacts(complete=False, reason="bucket 12 was never collected")),
        scope=NONE, scope_reason="scope reason",
    ),
)
_add(
    "new.history_scope_reason",
    _new(tb.novelty(history=HistoryFacts(complete=False)), scope=NONE, scope_reason="scope reason"),
)
_add(
    "new.history_scope_reason_coded",
    _new(
        tb.novelty(history=HistoryFacts(complete=False)),
        scope=NONE, scope_reason=SCOPE_NEEDS_EXACT,
    ),
)
_add("new.history_default", _new(tb.novelty(history=HistoryFacts(complete=False)), scope=NONE))
_add("new.flood_plain", _new(tb.novelty(appearance_flood="discovery truncated")))
_add("new.below_requests", _new(tb.novelty(requests_since_first_seen=19, buckets_with_traffic=5)))
_add("new.below_buckets", _new(tb.novelty(requests_since_first_seen=500, buckets_with_traffic=1)))
_add(
    "new.lower_below",
    _new(tb.novelty(requests_since_first_seen=3, buckets_with_traffic=1, lower_bound=True)),
)
_add("new.fired", _new())
_add("new.lower_fired", _new(tb.novelty(lower_bound=True)))
_add("new.fired_no_entity", lambda: detect_new_endpoint_access(_without_entity(tb.new_ctx())))
_add("new.fired_scope_inc", _new(scope=INC, scope_reason=SCOPE_INCREASE))


def _without_entity(ctx: tb.WafDetectionContext) -> tb.WafDetectionContext:
    ctx.extra = {}
    return ctx

