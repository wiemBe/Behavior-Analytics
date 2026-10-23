"""A refused Ariel permit is its own named, non-retryable failure."""

from __future__ import annotations

from app.models.enums import ExecutionErrorType
from app.providers.base import ProviderUnavailableError
from app.services.ariel_errors import NON_RETRYABLE, classify, is_retryable
from app.services.ariel_permit import ArielPermitUnavailable


def test_a_permit_refusal_is_capacity_not_network() -> None:
    refusal = ArielPermitUnavailable(30, 3, 6)
    assert isinstance(refusal, ProviderUnavailableError)
    assert classify(refusal) is ExecutionErrorType.CAPACITY
    # The generic unavailable error stays NETWORK and retryable.
    assert classify(ProviderUnavailableError("down")) is ExecutionErrorType.NETWORK


def test_capacity_is_never_retried() -> None:
    assert ExecutionErrorType.CAPACITY in NON_RETRYABLE
    assert not is_retryable(ExecutionErrorType.CAPACITY)
    assert is_retryable(ExecutionErrorType.NETWORK)
