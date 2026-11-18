"""Marking a classified source reviewed: shared by the single and the bulk routes."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.fingerprint import inventory_fingerprint
from app.alerts.routing import default_policy
from app.alerts.service import AlertResolution, AlertService
from app.i18n.messages import MessageRef
from app.models.enums import ClassificationReviewStatus
from app.models.fleet import LogSourceClassification
from app.models.log_source import LogSource
from app.repositories.audit import record_audit


async def review_classification(
    session: AsyncSession,
    ls: LogSource,
    row: LogSourceClassification,
    actor: str,
) -> bool:
    """Mark `row` reviewed and resolve the source's new-source alert.

    Idempotent: an already reviewed row changes nothing, writes no audit row and returns
    False. Classifying never resolves the alert; only this deliberate act does.
    """
    if row.review_status == ClassificationReviewStatus.REVIEWED:
        return False
    now = datetime.now(UTC)
    row.review_status = ClassificationReviewStatus.REVIEWED
    row.reviewed_at = now
    row.reviewed_by = actor
    await session.flush()
    _, resolved = await AlertService(session, clock=lambda: now).apply_bulk(
        [],
        [
            AlertResolution(
                fingerprint=inventory_fingerprint(ls.instance_id, ls.qradar_id),
                actor=actor,
                reason_message=MessageRef("alert.inventory.log_source.reviewed"),
            )
        ],
    )
    result = resolved[0]
    if result is not None and result.transition is not None:
        # Enqueue only: delivery belongs to the dispatch worker, as for the alert resolve route.
        await NotificationDispatcher(session, notifiers={}, policy=default_policy()).enqueue(
            result.alert, result.transition
        )
    await record_audit(
        session, action="log_source.review", actor=actor,
        object_type="log_source", object_id=str(ls.id),
        detail={"alert_resolved": result is not None},
    )
    return True
