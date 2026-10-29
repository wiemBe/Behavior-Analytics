"""Alert message metadata stays atomic with its English fallback."""

from __future__ import annotations

import uuid

import pytest

from app.alerts.service import AlertInput, AlertResolution, AlertService
from app.i18n.messages import MessageRef
from app.models.enums import Severity
from app.schemas.alert import AlertOut

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _input(fingerprint: str, message: MessageRef | None) -> AlertInput:
    return AlertInput(
        fingerprint=fingerprint,
        title="caller text",
        title_message=message,
        description="free description",
        severity=Severity.HIGH,
        source_type="test",
        source_id=uuid.uuid4(),
    )


async def test_direct_create_derives_english_and_refresh_clears_stale_metadata(
    db_session,
) -> None:
    service = AlertService(db_session)
    message = MessageRef("alert.threshold.crossed", {"name": "Daily failures"})
    created = await service.open_or_update(_input("msg7:direct", message))
    assert created.alert.title == "Daily failures crossed its threshold"
    assert created.alert.title_i18n == message.to_wire()

    refreshed = await service.open_or_update(_input("msg7:direct", None))
    assert refreshed.alert.title == "caller text"
    assert refreshed.alert.title_i18n is None


async def test_bulk_create_refresh_resolve_and_wire_fields(db_session) -> None:
    service = AlertService(db_session)
    title = MessageRef("alert.search.failure.title", {"name": "Daily failures"})
    opened, _ = await service.apply_bulk([_input("msg7:bulk", title)], [])
    refreshed, _ = await service.apply_bulk([_input("msg7:bulk", title)], [])
    reason = MessageRef("alert.search.failure.recovered")
    _, resolved = await service.apply_bulk(
        [],
        [AlertResolution(
            fingerprint="msg7:bulk", actor="test", reason="wrong", reason_message=reason
        )],
    )

    assert opened[0].alert.title_i18n == title.to_wire()
    assert refreshed[0].alert.title_i18n == title.to_wire()
    assert resolved[0] is not None
    alert = resolved[0].alert
    assert alert.resolution_reason == "search executed successfully"
    assert alert.resolution_reason_i18n == reason.to_wire()
    payload = AlertOut.model_validate(alert).model_dump(mode="json")
    assert payload["title_code"] == title.code
    assert payload["title_params"] == {"name": "Daily failures"}
    assert payload["resolution_reason_code"] == reason.code
    assert payload["resolution_reason_params"] == {}


async def test_direct_free_text_resolution_clears_metadata(db_session) -> None:
    service = AlertService(db_session)
    opened = await service.open_or_update(
        _input(
            "msg7:free",
            MessageRef("alert.threshold.crossed", {"name": "Daily failures"}),
        )
    )
    resolved = await service.resolve(opened.alert.id, actor="analyst", reason="handled")
    assert resolved.alert.resolution_reason == "handled"
    assert resolved.alert.resolution_reason_i18n is None
