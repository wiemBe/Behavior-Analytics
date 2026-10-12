"""Pure, deterministic log-source classification."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.models.enums import ClassificationRuleKind, SourceClass


@dataclass(frozen=True)
class SourceFacts:
    name: str
    type_id: int | None = None
    identifier: str | None = None
    group_ids: tuple[int, ...] = ()
    override_class: SourceClass | None = None


@dataclass(frozen=True)
class TypeFacts:
    type_id: int
    name: str
    internal: bool = False


@dataclass(frozen=True)
class GroupFacts:
    qradar_id: int
    parent_qradar_id: int | None = None


@dataclass(frozen=True)
class RuleFacts:
    id: uuid.UUID | str
    kind: ClassificationRuleKind
    pattern: str
    target_class: SourceClass
    priority: int = 0
    enabled: bool = True


@dataclass(frozen=True)
class ClassificationResult:
    source_class: SourceClass
    basis: str
    rule_id: uuid.UUID | str | None
    conflicts: tuple[dict[str, str | None], ...]


@dataclass(frozen=True)
class _Candidate:
    rule: RuleFacts

    @property
    def basis(self) -> str:
        return self.rule.kind.value


_LEVELS = (
    frozenset({ClassificationRuleKind.GROUP}),
    frozenset({ClassificationRuleKind.TYPE, ClassificationRuleKind.TYPE_NAME_REGEX}),
    frozenset({ClassificationRuleKind.NAME_REGEX, ClassificationRuleKind.IDENTIFIER_REGEX}),
)


def _ancestors(group_ids: tuple[int, ...], groups: dict[int, GroupFacts]) -> set[int]:
    """Return direct groups plus ancestors without mistaking id 0 for an ancestor.

    QRadar may use 0 both as a real leaf id and as the root sentinel. A source
    explicitly assigned to group 0 includes it. A non-zero root whose parent is
    0 stops there instead of descending into a real group-0 row.
    """
    found: set[int] = set()
    for start in group_ids:
        current = start
        branch: set[int] = set()
        while current not in branch:
            branch.add(current)
            found.add(current)
            group = groups.get(current)
            if group is None or group.parent_qradar_id is None:
                break
            parent = group.parent_qradar_id
            if parent == current or (parent == 0 and current != 0):
                break
            current = parent
    return found


def _regex_matches(pattern: str, value: str | None) -> bool:
    if value is None:
        return False
    try:
        return re.search(pattern, value, flags=re.IGNORECASE) is not None
    except re.error:
        return False


def _matches(
    rule: RuleFacts,
    source: SourceFacts,
    type_fact: TypeFacts | None,
    group_ids: set[int],
) -> bool:
    if rule.kind == ClassificationRuleKind.GROUP:
        try:
            return int(rule.pattern) in group_ids
        except ValueError:
            return False
    if rule.kind == ClassificationRuleKind.TYPE:
        return source.type_id is not None and rule.pattern.strip() == str(source.type_id)
    if rule.kind == ClassificationRuleKind.TYPE_NAME_REGEX:
        return _regex_matches(rule.pattern, type_fact.name if type_fact else None)
    if rule.kind == ClassificationRuleKind.NAME_REGEX:
        return _regex_matches(rule.pattern, source.name)
    if rule.kind == ClassificationRuleKind.IDENTIFIER_REGEX:
        return _regex_matches(rule.pattern, source.identifier)
    return False


def _conflict(candidate: _Candidate) -> dict[str, str | None]:
    return {
        "basis": candidate.basis,
        "rule_id": str(candidate.rule.id),
        "class": candidate.rule.target_class.value,
    }


def classify(
    source: SourceFacts,
    rules: tuple[RuleFacts, ...] | list[RuleFacts],
    types: tuple[TypeFacts, ...] | list[TypeFacts],
    groups: tuple[GroupFacts, ...] | list[GroupFacts],
) -> ClassificationResult:
    """Classify one source according to the Phase C precedence contract."""
    type_fact = next((item for item in types if item.type_id == source.type_id), None)
    group_ids = _ancestors(source.group_ids, {item.qradar_id: item for item in groups})
    candidates = [
        _Candidate(rule)
        for rule in rules
        if rule.enabled and _matches(rule, source, type_fact, group_ids)
    ]
    candidates.sort(
        key=lambda item: (
            -item.rule.priority,
            item.basis,
            item.rule.pattern,
            str(item.rule.id),
        )
    )

    winner_class: SourceClass | None = None
    winner_basis = SourceClass.UNCLASSIFIED.value
    winner_rule_id: uuid.UUID | str | None = None
    winner_level = len(_LEVELS)
    conflicts: list[dict[str, str | None]] = []

    if source.override_class is not None:
        winner_class = source.override_class
        winner_basis = "OVERRIDE"
        winner_level = -1
    elif (type_fact is not None and type_fact.internal) or source.identifier == "127.0.0.1":
        winner_class = SourceClass.QRADAR_INTERNAL
        winner_basis = "INTERNAL"
        winner_level = -1
    else:
        for level_index, kinds in enumerate(_LEVELS):
            level = [item for item in candidates if item.rule.kind in kinds]
            if not level:
                continue
            top_priority = max(item.rule.priority for item in level)
            top = [item for item in level if item.rule.priority == top_priority]
            top_classes = {item.rule.target_class for item in top}
            if len(top_classes) > 1:
                conflicts.extend(_conflict(item) for item in top)
                continue
            chosen = top[0]
            winner_class = chosen.rule.target_class
            winner_basis = chosen.basis
            winner_rule_id = chosen.rule.id
            winner_level = level_index
            break

    if winner_class is None:
        winner_class = SourceClass.UNCLASSIFIED

    for level_index, kinds in enumerate(_LEVELS):
        if level_index <= winner_level:
            continue
        for candidate in candidates:
            if (
                candidate.rule.kind in kinds
                and candidate.rule.target_class != winner_class
            ):
                conflicts.append(_conflict(candidate))

    unique = {
        (item["basis"], item["rule_id"], item["class"]): item for item in conflicts
    }
    ordered = tuple(unique[key] for key in sorted(unique))
    return ClassificationResult(winner_class, winner_basis, winner_rule_id, ordered)
