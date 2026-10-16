"""The metric catalog: what a custom rule may threshold, and what it proves.

One entry per §F.1.1 row — the ten per-bucket aggregates the platform already
collects, and nothing else. This is the F.1 analogue of `DetectorSpec`
(`app.anomaly.capabilities`): the rule's verdict scope comes from the catalog,
never from the evaluator's own reasoning (AGENTS.md rule 8, second half). A
metric absent here is not a rule target; validation refuses it, and a guard
test pins the catalog's shape (design §F.14).

Pure. No I/O, no ORM.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models.enums import CustomRuleMetric, CustomRuleSubjectKind


class MeasurementRule(StrEnum):
    """What a rule may conclude from a bucket whose count is a lower bound.

    The requirement vocabulary of `app.anomaly.capabilities` (§F.3.2): a count
    that can only understate the truth may prove "above the threshold" — every
    guard on the way there is non-decreasing in the count — and can never prove
    "below" or "equal".
    """

    #: Every verdict needs an exact count. A lower bound bounds nothing below.
    EXACT_ONLY = "EXACT_ONLY"
    #: GT/GE verdicts may be proven from a lower bound; LT/LE/EQ never are.
    LOWER_BOUND_PROVES_INCREASE = "LOWER_BOUND_PROVES_INCREASE"


@dataclass(frozen=True)
class MetricCatalogEntry:
    """One legal rule metric (one row of design §F.1.1)."""

    metric: CustomRuleMetric
    subject_kind: CustomRuleSubjectKind
    #: Display unit, e.g. "events/s"; ratios are a "share" in [0, 1].
    unit: str
    #: The stored columns the metric reads, as ``table.column`` strings.
    reads: tuple[str, ...]
    measurement: MeasurementRule
    #: Ratios need exact denominators and their thresholds lie in (0, 1).
    is_ratio: bool
    #: The bulk loader that feeds this kind (design §F.4.2), keyed by kind.
    bulk_loader: str
    #: The honesty note the rule editor shows next to the metric (§F.12).
    note: str


_CUSTOM_RULE_METRICS: tuple[MetricCatalogEntry, ...] = (
    MetricCatalogEntry(
        metric=CustomRuleMetric.EVENT_COUNT,
        subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
        unit="events/bucket",
        reads=("log_source_metric.event_count",),
        measurement=MeasurementRule.EXACT_ONLY,
        is_ratio=False,
        bulk_loader="LOG_SOURCE",
        note=(
            "exact only when the bucket row exists and is COMPLETE; a PARTIAL "
            "bucket proves nothing either way"
        ),
    ),
    MetricCatalogEntry(
        metric=CustomRuleMetric.AVERAGE_EPS,
        subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
        unit="events/s",
        reads=("log_source_metric.average_eps",),
        measurement=MeasurementRule.EXACT_ONLY,
        is_ratio=False,
        bulk_loader="LOG_SOURCE",
        note=(
            "exact only when the bucket row exists and is COMPLETE; a PARTIAL "
            "bucket proves nothing either way"
        ),
    ),
    MetricCatalogEntry(
        metric=CustomRuleMetric.EPS,
        subject_kind=CustomRuleSubjectKind.FLEET_CLASS,
        unit="events/s",
        reads=("log_source_metric.event_count", "log_source_metric.bucket_seconds"),
        measurement=MeasurementRule.LOWER_BOUND_PROVES_INCREASE,
        is_ratio=False,
        bulk_loader="FLEET_CLASS",
        note=(
            "a lower bound unless every enabled, monitored source of the class "
            "has a COMPLETE bucket: missing sources can only add events"
        ),
    ),
    MetricCatalogEntry(
        metric=CustomRuleMetric.REQUEST_COUNT,
        subject_kind=CustomRuleSubjectKind.WAF_ENDPOINT,
        unit="requests/bucket",
        reads=("waf_endpoint_metric.request_count",),
        measurement=MeasurementRule.LOWER_BOUND_PROVES_INCREASE,
        is_ratio=False,
        bulk_loader="WAF_ENDPOINT",
        note=(
            "SUM(eventcount), never COUNT(*); a bucket whose grouping truncated "
            "or fell below the coverage floor proves 'above' only"
        ),
    ),
    MetricCatalogEntry(
        metric=CustomRuleMetric.BLOCK_RATIO,
        subject_kind=CustomRuleSubjectKind.WAF_ENDPOINT,
        unit="share",
        reads=(
            "waf_endpoint_metric.blocked_count",
            "waf_endpoint_metric.action_events",
            "waf_endpoint_metric.unmapped_action_count",
        ),
        measurement=MeasurementRule.LOWER_BOUND_PROVES_INCREASE,
        is_ratio=True,
        bulk_loader="WAF_ENDPOINT",
        note=(
            "blocked requests over requests carrying an action value; an "
            "unmapped action value makes the numerator a lower bound"
        ),
    ),
    MetricCatalogEntry(
        metric=CustomRuleMetric.SERVER_ERROR_RATIO,
        subject_kind=CustomRuleSubjectKind.WAF_ENDPOINT,
        unit="share",
        reads=(
            "waf_endpoint_metric.server_error_count",
            "waf_endpoint_metric.response_code_events",
            "waf_endpoint_metric.unclassified_response_count",
        ),
        measurement=MeasurementRule.LOWER_BOUND_PROVES_INCREASE,
        is_ratio=True,
        bulk_loader="WAF_ENDPOINT",
        note=(
            "5xx responses over responses carrying a response code; an "
            "unclassified response code makes the numerator a lower bound"
        ),
    ),
    MetricCatalogEntry(
        metric=CustomRuleMetric.CLIENT_ERROR_RATIO,
        subject_kind=CustomRuleSubjectKind.WAF_ENDPOINT,
        unit="share",
        reads=(
            "waf_endpoint_metric.client_error_count",
            "waf_endpoint_metric.response_code_events",
            "waf_endpoint_metric.unclassified_response_count",
        ),
        measurement=MeasurementRule.LOWER_BOUND_PROVES_INCREASE,
        is_ratio=True,
        bulk_loader="WAF_ENDPOINT",
        note=(
            "4xx responses over responses carrying a response code; an "
            "unclassified response code makes the numerator a lower bound"
        ),
    ),
    MetricCatalogEntry(
        metric=CustomRuleMetric.EVENTS,
        subject_kind=CustomRuleSubjectKind.IPS_ENTITY,
        unit="events/bucket",
        reads=("ips_entity_metric.events",),
        measurement=MeasurementRule.LOWER_BOUND_PROVES_INCREASE,
        is_ratio=False,
        bulk_loader="IPS_ENTITY",
        note=(
            "sparse per admitted entity: no row means not measured; a bucket "
            "whose dimension search truncated proves 'above' only"
        ),
    ),
    MetricCatalogEntry(
        metric=CustomRuleMetric.TOTAL_EVENTS,
        subject_kind=CustomRuleSubjectKind.IPS_SOURCE,
        unit="events/bucket",
        reads=("ips_source_bucket.total_events",),
        measurement=MeasurementRule.EXACT_ONLY,
        is_ratio=False,
        bulk_loader="IPS_SOURCE",
        note=(
            "exact whenever the bucket row exists: totals are complete or the "
            "row is absent"
        ),
    ),
    MetricCatalogEntry(
        metric=CustomRuleMetric.BLOCKED_SHARE,
        subject_kind=CustomRuleSubjectKind.IPS_SOURCE,
        unit="share",
        reads=(
            "ips_source_bucket.blocked_events",
            "ips_source_bucket.allowed_events",
            "ips_source_bucket.unmapped_events",
        ),
        measurement=MeasurementRule.LOWER_BOUND_PROVES_INCREASE,
        is_ratio=True,
        bulk_loader="IPS_SOURCE",
        note=(
            "blocked events over action events; an unmapped action value makes "
            "the numerator a lower bound"
        ),
    ),
)

#: Every legal rule metric, keyed by metric. Exactly the ten rows of §F.1.1.
CUSTOM_RULE_METRICS: dict[CustomRuleMetric, MetricCatalogEntry] = {
    entry.metric: entry for entry in _CUSTOM_RULE_METRICS
}


def catalog_entry(metric: CustomRuleMetric) -> MetricCatalogEntry:
    """The catalog entry for `metric`; KeyError names the problem for callers."""
    try:
        return CUSTOM_RULE_METRICS[metric]
    except KeyError:
        known = ", ".join(m.value for m in CUSTOM_RULE_METRICS)
        raise KeyError(f"{metric.value} is not a custom-rule metric (known: {known})") from None


def metrics_for_kind(kind: CustomRuleSubjectKind) -> list[CustomRuleMetric]:
    """The legal metrics for one subject kind, in catalog order."""
    return [
        entry.metric
        for entry in _CUSTOM_RULE_METRICS
        if entry.subject_kind is kind
    ]


__all__ = [
    "CUSTOM_RULE_METRICS",
    "MeasurementRule",
    "MetricCatalogEntry",
    "catalog_entry",
    "metrics_for_kind",
]
