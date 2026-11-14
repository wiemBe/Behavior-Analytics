"""Tokens and the transport split for identity revalidation. No database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.identity.dto import IdentityFieldProbe, IdentityProbeResult
from app.identity.revalidation import failure_token, revalidation_slot, transport_reason

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _probe(**over: object) -> IdentityProbeResult:
    values: dict[str, object] = {
        "total_events": 100,
        "event_id": IdentityFieldProbe(95, False, None),
        "username": IdentityFieldProbe(92, False, None),
        "catalog_event_ids": (4625,),
        "truncated": False,
        "error": None,
        "searches": 4,
    }
    values.update(over)
    return IdentityProbeResult(**values)  # type: ignore[arg-type]


def test_failure_token_keeps_a_short_class_and_drops_a_message() -> None:
    assert failure_token("ProviderError") == "provider_error"
    assert failure_token("ArielPermitUnavailable") == "permit_refused"
    assert failure_token("ProviderUnavailableError") == "provider_unavailable"
    assert failure_token("TimeoutError") == "timeout"
    assert failure_token("_Boom") == "_Boom"
    assert failure_token("alice searched") == "probe_failed"
    assert failure_token("A" * 40) == "probe_failed"
    assert len(failure_token("ProviderUnavailableError")) <= 23


def test_transport_reason_lets_a_rejected_field_through() -> None:
    probe = _probe(
        event_id=IdentityFieldProbe(None, True, "ProviderQueryRejectedError"),
        error="ProviderUnavailableError",
    )
    assert transport_reason(probe) is None


def test_transport_reason_uses_a_field_error_when_nothing_was_rejected() -> None:
    probe = _probe(username=IdentityFieldProbe(None, False, "ProviderUnavailableError"))
    assert transport_reason(probe) == "provider_unavailable"


def test_transport_reason_is_empty_for_a_finished_probe() -> None:
    assert transport_reason(_probe()) is None
    assert transport_reason(_probe(total_events=0)) is None
    assert transport_reason(_probe(truncated=True)) is None


def test_revalidation_slot_is_the_epoch_bucket() -> None:
    interval = 86400
    assert revalidation_slot(NOW, interval) == int(NOW.timestamp()) // interval
    assert revalidation_slot(NOW + timedelta(seconds=interval), interval) == (
        revalidation_slot(NOW, interval) + 1
    )
    with pytest.raises(ValueError):
        revalidation_slot(NOW.replace(tzinfo=None), interval)
    with pytest.raises(ValueError):
        revalidation_slot(NOW, 0)
