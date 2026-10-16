"""Pure validation for a custom-rule draft (design §F.3.1, §F.14).

One function returns every problem at once, as reason strings, the
`AQLValidationError.reasons` pattern: the API (F-3) folds them into its 422/409
responses, and nothing here touches the database. The subject-column pairing is
the same rule the migration's CHECK enforces, stated for operators in words.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from app.custom_rules.catalog import CUSTOM_RULE_METRICS
from app.models.enums import CustomRuleMetric, CustomRuleSubjectKind, SourceClass

#: The scheduled-search operator set (`app/schemas/search.py`).
RULE_OPERATORS = frozenset({"GT", "GE", "LT", "LE", "EQ"})

#: Every subject column, by kind: exactly these are set, the rest are NULL.
_SUBJECT_COLUMNS: dict[CustomRuleSubjectKind, tuple[str, ...]] = {
    CustomRuleSubjectKind.LOG_SOURCE: ("log_source_id",),
    CustomRuleSubjectKind.FLEET_CLASS: ("source_class",),
    CustomRuleSubjectKind.WAF_ENDPOINT: ("waf_endpoint_id",),
    CustomRuleSubjectKind.IPS_ENTITY: ("ips_entity_id",),
    CustomRuleSubjectKind.IPS_SOURCE: ("log_source_id",),
}


@dataclass(frozen=True)
class CustomRuleDraft:
    """What a rule proposes, before it exists (create or edit)."""

    subject_kind: CustomRuleSubjectKind
    #: A catalog metric, or any string the API received. An unknown string is a
    #: reason, not an exception: the draft is data, and validation reports it.
    metric: CustomRuleMetric | str
    operator: str
    threshold_value: float
    sustain_buckets: int
    recovery_buckets: int
    log_source_id: uuid.UUID | None = None
    source_class: str | None = None
    waf_endpoint_id: uuid.UUID | None = None
    ips_entity_id: uuid.UUID | None = None
    enabled: bool = False


def validate_rule_draft(
    draft: CustomRuleDraft,
    *,
    enabled_count: int = 0,
    previously_enabled: bool = False,
    max_enabled: int = 100,
) -> list[str]:
    """Every reason the draft is not a legal rule; empty means it is one.

    `enabled_count` is the number of enabled rules that exist; the cap refuses
    creating or enabling beyond `max_enabled`, naming the limit (design §F.4.2).
    `previously_enabled` lets an edit keep its own enabled slot.
    """
    reasons: list[str] = []

    # The subject-column pairing, per kind.
    expected = _SUBJECT_COLUMNS[draft.subject_kind]
    columns = {
        "log_source_id": draft.log_source_id,
        "source_class": draft.source_class,
        "waf_endpoint_id": draft.waf_endpoint_id,
        "ips_entity_id": draft.ips_entity_id,
    }
    for name, value in columns.items():
        if name in expected and value is None:
            reasons.append(
                f"{draft.subject_kind.value} rules need {name} set"
            )
        if name not in expected and value is not None:
            reasons.append(
                f"{name} must stay empty for a {draft.subject_kind.value} rule"
            )
    if (
        draft.subject_kind is CustomRuleSubjectKind.FLEET_CLASS
        and draft.source_class is not None
        and draft.source_class not in {member.value for member in SourceClass}
    ):
        reasons.append(
            f"source_class {draft.source_class!r} is not a known source class"
        )

    # The metric belongs to the kind (the catalog decides, not the evaluator).
    metric = _known_metric(draft.metric)
    entry = CUSTOM_RULE_METRICS[metric] if metric is not None else None
    if metric is None or entry is None:
        reasons.append(f"metric {draft.metric!r} is not in the custom-rule catalog")
    elif entry.subject_kind is not draft.subject_kind:
        reasons.append(
            f"metric {metric.value} measures a "
            f"{entry.subject_kind.value} subject, not a {draft.subject_kind.value} one"
        )

    # The threshold: finite and non-negative for every metric, strictly inside
    # (0, 1) for a share. A ratio threshold of 0 or 1 can never fire or never
    # recover, and a non-finite value compares true against nothing — which the
    # fold would otherwise read as "under" and use to resolve a breach.
    finite = isinstance(draft.threshold_value, int | float) and math.isfinite(
        draft.threshold_value
    )
    if not finite:
        reasons.append("threshold_value must be a finite number")
    elif draft.threshold_value < 0:
        reasons.append("threshold_value must be at least 0")
    if metric is not None and entry is not None and entry.is_ratio and finite and not (
        0.0 < draft.threshold_value < 1.0
    ):
        reasons.append(
            f"a {metric.value} threshold is a share and must lie strictly between 0 and 1"
        )

    if draft.operator not in RULE_OPERATORS:
        reasons.append(
            f"operator {draft.operator!r} is not one of GT, GE, LT, LE, EQ"
        )

    if draft.sustain_buckets < 1:
        reasons.append("sustain_buckets must be at least 1")
    if draft.recovery_buckets < 1:
        reasons.append("recovery_buckets must be at least 1")

    # The enabled-rule cap, counting the slot this draft already holds.
    keeps_slot = draft.enabled and previously_enabled
    if draft.enabled and not keeps_slot and enabled_count >= max_enabled:
        reasons.append(
            f"at most {max_enabled} rules may be enabled at once "
            f"({enabled_count} already are)"
        )

    return reasons


def _known_metric(value: CustomRuleMetric | str) -> CustomRuleMetric | None:
    """The catalog member `value` names, or None when it names nothing."""
    if isinstance(value, CustomRuleMetric):
        candidate = value
    else:
        try:
            candidate = CustomRuleMetric(str(value))
        except ValueError:
            return None
    if candidate not in CUSTOM_RULE_METRICS:
        return None
    return candidate


__all__ = ["RULE_OPERATORS", "CustomRuleDraft", "validate_rule_draft"]
