"""Bulk persistence for log-source classification."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.fleet.classification import (
    ClassificationResult,
    GroupFacts,
    RuleFacts,
    SourceFacts,
    TypeFacts,
    classify,
)
from app.fleet.seed_rules import SEED_RULES
from app.models.enums import (
    ClassificationReviewStatus,
    ClassificationRuleKind,
    SourceClass,
)
from app.models.fleet import (
    ClassificationRule,
    LogSourceClassification,
    LogSourceGroup,
    LogSourceType,
)
from app.models.log_source import LogSource


@dataclass(frozen=True)
class AppliedClassification:
    result: ClassificationResult
    review_status: ClassificationReviewStatus


async def ensure_seed_rules(session: AsyncSession) -> None:
    """Upsert built-in definitions while preserving the operator's enabled state."""
    rows = [
        {
            "id": item.id,
            "instance_id": None,
            "kind": item.kind.value,
            "pattern": item.pattern,
            "target_class": item.target_class.value,
            "priority": item.priority,
            "enabled": True,
            "built_in": True,
            "description": item.description,
            "created_by": None,
        }
        for item in SEED_RULES
    ]
    statement = pg_insert(ClassificationRule).values(rows)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[ClassificationRule.id],
            set_={
                "kind": statement.excluded.kind,
                "pattern": statement.excluded.pattern,
                "target_class": statement.excluded.target_class,
                "priority": statement.excluded.priority,
                "description": statement.excluded.description,
            },
            where=ClassificationRule.built_in.is_(True),
        )
    )


async def classify_sources(
    session: AsyncSession,
    *,
    instance_id: uuid.UUID,
    sources: list[LogSource],
    types: list[LogSourceType],
    groups: list[LogSourceGroup],
    new_source_ids: set[uuid.UUID],
    bootstrap: bool,
    classified_at: datetime,
) -> dict[uuid.UUID, AppliedClassification]:
    """Classify and upsert a whole instance with one statement per table."""
    await ensure_seed_rules(session)
    rule_rows = list(
        (
            await session.scalars(
                select(ClassificationRule).where(
                    ClassificationRule.enabled.is_(True),
                    or_(
                        ClassificationRule.instance_id.is_(None),
                        ClassificationRule.instance_id == instance_id,
                    ),
                )
            )
        ).all()
    )
    existing = {
        row.log_source_id: row
        for row in (
            await session.scalars(
                select(LogSourceClassification).where(
                    LogSourceClassification.log_source_id.in_(
                        [source.id for source in sources]
                    )
                )
            )
        ).all()
    }

    type_facts = [
        TypeFacts(type_id=row.type_id, name=row.name, internal=row.internal) for row in types
    ]
    group_facts = [
        GroupFacts(qradar_id=row.qradar_id, parent_qradar_id=row.parent_qradar_id)
        for row in groups
    ]
    rule_facts = [
        RuleFacts(
            id=row.id,
            kind=ClassificationRuleKind(row.kind),
            pattern=row.pattern,
            target_class=SourceClass(row.target_class),
            priority=row.priority,
            enabled=row.enabled,
        )
        for row in rule_rows
    ]

    values: list[dict] = []
    applied: dict[uuid.UUID, AppliedClassification] = {}
    for source in sources:
        current = existing.get(source.id)
        override = (
            SourceClass(current.override_class)
            if current and current.override_class
            else None
        )
        result = classify(
            SourceFacts(
                name=source.name,
                type_id=source.type_id,
                identifier=source.identifier,
                group_ids=tuple(source.group_ids or ()),
                override_class=override,
            ),
            rule_facts,
            type_facts,
            group_facts,
        )
        if current is not None:
            review_status = ClassificationReviewStatus(current.review_status)
        elif (
            source.id in new_source_ids
            and not bootstrap
            and result.source_class != SourceClass.QRADAR_INTERNAL
        ):
            review_status = ClassificationReviewStatus.NEW
        else:
            review_status = ClassificationReviewStatus.REVIEWED

        values.append(
            {
                "log_source_id": source.id,
                "source_class": result.source_class.value,
                "basis": result.basis,
                "rule_id": result.rule_id,
                "override_class": override.value if override else None,
                "override_by": current.override_by if current else None,
                "override_at": current.override_at if current else None,
                "conflicts": list(result.conflicts),
                "classified_at": classified_at,
                "review_status": review_status.value,
                "reviewed_at": current.reviewed_at if current else None,
                "reviewed_by": current.reviewed_by if current else None,
            }
        )
        applied[source.id] = AppliedClassification(result, review_status)

    if values:
        statement = pg_insert(LogSourceClassification).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[LogSourceClassification.log_source_id],
            set_={
                "source_class": statement.excluded.source_class,
                "basis": statement.excluded.basis,
                "rule_id": statement.excluded.rule_id,
                "conflicts": statement.excluded.conflicts,
                "classified_at": statement.excluded.classified_at,
            },
        )
        await session.execute(statement)
    return applied
