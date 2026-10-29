"""Map provider/transport failures to the ExecutionErrorType taxonomy.

The spec requires distinguishing validation, auth, rate-limiting, timeout, search
failure, network and parsing failures — because each drives different retry and
alerting behaviour. This module centralises that classification so the executor
and the provider stay decoupled.
"""

from __future__ import annotations

import httpx

from app.i18n.messages import MessageRef, render
from app.models.enums import ExecutionErrorType
from app.providers.base import ProviderAuthError, ProviderUnavailableError
from app.services.aql_validator import AQLValidationError
from app.services.ariel_permit import ArielPermitUnavailable


class _CodedError(RuntimeError):
    """A run failure whose fixed sentence is also a message code (MSG-8b).

    `ref` is the code; the exception text is its English render, so the two cannot disagree.
    Built with a plain string instead, it is free text and carries no code.
    """

    ref: MessageRef | None = None

    def __init__(self, message: str | MessageRef) -> None:
        if isinstance(message, MessageRef):
            self.ref = message
            message = render(message)
        super().__init__(message)


class ArielSearchFailed(_CodedError):
    """QRadar reported the Ariel search itself entered an error state."""


class ArielTimeout(_CodedError):
    """The search exceeded our execution timeout."""


class ResultParsingError(_CodedError):
    """The provider returned results we could not normalise."""


def failure_ref(exc: BaseException) -> MessageRef | None:
    """The message code of a run failure's text, or None when the text is free text.

    The code is returned only when its English render is exactly ``str(exc)``: a text that
    cannot be reproduced from its code never gets one.
    """
    ref: MessageRef | None = getattr(exc, "ref", None)
    # The defensive re-validation of a stored query: coded only when every reason is.
    if (
        ref is None
        and isinstance(exc, AQLValidationError)
        and exc.refs
        and all(item is not None for item in exc.refs)
    ):
        ref = MessageRef("search.run.validation", {"reasons": list(exc.refs)})
    if not isinstance(ref, MessageRef):
        return None
    try:
        return ref if render(ref) == str(exc) else None
    except ValueError:
        return None


# Failure modes that must NEVER be retried (they are deterministic).
NON_RETRYABLE = {
    ExecutionErrorType.VALIDATION,
    ExecutionErrorType.AUTH,
    ExecutionErrorType.PARSING,
    ExecutionErrorType.CANCELLED,
    # A full budget is a decision to wait for the next cycle, not to hold the permit
    # queue longer: a retry would only wait again behind the same searches.
    ExecutionErrorType.CAPACITY,
}


def classify(exc: BaseException) -> ExecutionErrorType:
    """Classify an exception into the execution error taxonomy."""
    if isinstance(exc, AQLValidationError):
        return ExecutionErrorType.VALIDATION
    # Before ProviderUnavailableError, which it subclasses.
    if isinstance(exc, ArielPermitUnavailable):
        return ExecutionErrorType.CAPACITY
    if isinstance(exc, ProviderAuthError):
        return ExecutionErrorType.AUTH
    if isinstance(exc, ArielTimeout):
        return ExecutionErrorType.TIMEOUT
    if isinstance(exc, ArielSearchFailed):
        return ExecutionErrorType.SEARCH_FAILED
    if isinstance(exc, ResultParsingError):
        return ExecutionErrorType.PARSING
    if isinstance(exc, ProviderUnavailableError):
        return ExecutionErrorType.NETWORK
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return ExecutionErrorType.AUTH
        if status == 429:
            return ExecutionErrorType.RATE_LIMITED
        if status >= 500:
            return ExecutionErrorType.NETWORK
        return ExecutionErrorType.SEARCH_FAILED
    if isinstance(exc, (httpx.TimeoutException,)):
        return ExecutionErrorType.TIMEOUT
    if isinstance(exc, (httpx.TransportError, ConnectionError)):
        return ExecutionErrorType.NETWORK
    # Unknown — treat as network so it retries a bounded number of times rather
    # than being permanently non-retryable.
    return ExecutionErrorType.NETWORK


def is_retryable(error_type: ExecutionErrorType) -> bool:
    return error_type not in NON_RETRYABLE
