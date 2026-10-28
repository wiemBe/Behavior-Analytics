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
from app.i18n.messages import MessageRef, render
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
    CustomRuleSubjectKind.PW_VALUE: ("pw_value_id",),
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
    pw_value_id: uuid.UUID | None = None
    enabled: bool = False


def validate_rule_draft(
    draft: CustomRuleDraft,
    *,
    enabled_count: int = 0,
    previously_enabled: bool = False,
    max_enabled: int = 100,
) -> list[str]:
    """Every reason the draft is not a legal rule, as English sentences; empty means legal."""
    return [
        render(reason)
        for reason in validate_rule_draft_coded(
            draft,
            enabled_count=enabled_count,
            previously_enabled=previously_enabled,
            max_enabled=max_enabled,
        )
    ]


def validate_rule_draft_coded(
    draft: CustomRuleDraft,
    *,
    enabled_count: int = 0,
    previously_enabled: bool = False,
    max_enabled: int = 100,
) -> list[MessageRef]:
    """Every reason the draft is not a legal rule, each a message code; empty means it is one.

    `enabled_count` is the number of enabled rules that exist; the cap refuses
    creating or enabling beyond `max_enabled`, naming the limit (design §F.4.2).
    `previously_enabled` lets an edit keep its own enabled slot.
    """
    reasons: list[MessageRef] = []

    # The subject-column pairing, per kind.
    expected = _SUBJECT_COLUMNS[draft.subject_kind]
    columns = {
        "log_source_id": draft.log_source_id,
        "source_class": draft.source_class,
        "waf_endpoint_id": draft.waf_endpoint_id,
        "ips_entity_id": draft.ips_entity_id,
        "pw_value_id": draft.pw_value_id,
    }
    for name, value in columns.items():
        if name in expected and value is None:
            reasons.append(
                MessageRef(
                    "ruledraft.subject_needs_column",
                    {"kind": draft.subject_kind.value, "column": name},
                )
            )
        if name not in expected and value is not None:
            reasons.append(
                MessageRef(
                    "ruledraft.column_must_be_empty",
                    {"column": name, "kind": draft.subject_kind.value},
                )
            )
    if (
        draft.subject_kind is CustomRuleSubjectKind.FLEET_CLASS
        and draft.source_class is not None
        and draft.source_class not in {member.value for member in SourceClass}
    ):
        reasons.append(
            MessageRef(
                "ruledraft.source_class_unknown", {"value": repr(draft.source_class)}
            )
        )

    # The metric belongs to the kind (the catalog decides, not the evaluator).
    metric = _known_metric(draft.metric)
    entry = CUSTOM_RULE_METRICS[metric] if metric is not None else None
    if metric is None or entry is None:
        reasons.append(
            MessageRef("ruledraft.metric_unknown", {"metric": repr(draft.metric)})
        )
    elif entry.subject_kind is not draft.subject_kind:
        reasons.append(
            MessageRef(
                "ruledraft.metric_wrong_kind",
                {
                    "metric": metric.value,
                    "metric_kind": entry.subject_kind.value,
                    "kind": draft.subject_kind.value,
                },
            )
        )

    # The threshold: finite and non-negative for every metric, strictly inside
    # (0, 1) for a share. A ratio threshold of 0 or 1 can never fire or never
    # recover, and a non-finite value compares true against nothing — which the
    # fold would otherwise read as "under" and use to resolve a breach.
    finite = isinstance(draft.threshold_value, int | float) and math.isfinite(
        draft.threshold_value
    )
    if not finite:
        reasons.append(MessageRef("ruledraft.threshold_not_finite"))
    elif draft.threshold_value < 0:
        reasons.append(MessageRef("ruledraft.threshold_negative"))
    if metric is not None and entry is not None and entry.is_ratio and finite and not (
        0.0 < draft.threshold_value < 1.0
    ):
        reasons.append(MessageRef("ruledraft.threshold_share", {"metric": metric.value}))

    if draft.operator not in RULE_OPERATORS:
        reasons.append(
            MessageRef("ruledraft.operator_unknown", {"operator": repr(draft.operator)})
        )

    if draft.sustain_buckets < 1:
        reasons.append(MessageRef("ruledraft.sustain_min"))
    if draft.recovery_buckets < 1:
        reasons.append(MessageRef("ruledraft.recovery_min"))

    # The enabled-rule cap, counting the slot this draft already holds.
    keeps_slot = draft.enabled and previously_enabled
    if draft.enabled and not keeps_slot and enabled_count >= max_enabled:
        reasons.append(
            MessageRef(
                "ruledraft.enabled_cap",
                {"max_enabled": max_enabled, "enabled_count": enabled_count},
            )
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


__all__ = ["RULE_OPERATORS", "CustomRuleDraft", "validate_rule_draft", "validate_rule_draft_coded"]
