"""The versioned generic-webhook payload, v1 (SOC-12 design §4).

One body per event -- ``opened``, ``acknowledged``, ``resolved`` and ``test`` -- built
only from the fields the design lists. The bytes are deterministic: the keys are
inserted in one fixed order and the JSON has compact separators, so the same input
always yields the same body. That is what ``sign_v1`` signs and what lets a peer drop
a retry it already received after a timeout.

Times are ISO-8601 UTC with a literal ``Z`` suffix (never ``+00:00``), because the
body's bytes are the signed artifact. Microseconds are kept only when the source
value has them, so a whole-second time stays ``09:00:00`` and a sub-second time is
not silently truncated.

Nothing raw leaves: there is no ``evidence_snapshot``, no ``source_anomaly_ids``, no
raw event, path or address, no AQL and no URL but ``public_origin`` + ``/alerts/{id}``.
The signature secret is a parameter and is never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from app.models.enums import AlertStatus, AlertTransition, Severity

#: The contract version the body and the idempotency key belong to.
SCHEMA_VERSION = 1

#: Header the peer verifies the body with (``v1=<hex>``, ``sign_v1``).
SIGNATURE_HEADER = "X-QRadar-Dash-Signature"

CONTENT_TYPE = "application/json"

#: ``AlertDispatcher._render`` stores the source id in this form: ``redact()`` masks a
#: bare UUID as token-shaped, exactly as it masks ``alert_id``. The dispatcher cannot
#: pass the column here at delivery time (only the notification row is loaded), so the
#: payload keeps the id in a form the redaction pass leaves alone, and the builder
#: emits the bare UUID the contract names.
SOURCE_ID_PREFIX = "urn:uuid:"

#: The same reason, for ``acknowledged_by`` and ``resolved_by``: a username of 24 or
#: more letters, digits, hyphens or underscores is token-shaped to ``redact()``, and a
#: masked principal in the body is worse than a prefixed one in the payload.
PRINCIPAL_PREFIX = "principal:"

_EVENT_BY_TRANSITION: dict[AlertTransition, str] = {
    AlertTransition.OPENED: "opened",
    AlertTransition.ACKNOWLEDGED: "acknowledged",
    AlertTransition.RESOLVED: "resolved",
}

#: The payload key that carries the event's own UTC time.
_TIME_KEY_BY_TRANSITION: dict[AlertTransition, str] = {
    AlertTransition.OPENED: "opened_at",
    AlertTransition.ACKNOWLEDGED: "acknowledged_at",
    AlertTransition.RESOLVED: "resolved_at",
}


def _utc_text(value: object) -> str:
    """One ISO-8601 UTC rendering: ``YYYY-MM-DDTHH:MM:SS[.ffffff]Z``."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"not an ISO-8601 time: {value!r}") from exc
    else:
        raise ValueError(f"not an ISO-8601 time: {value!r}")
    if moment.tzinfo is None or moment.utcoffset() is None:
        # A naive time is ambiguous; never guess UTC for it.
        raise ValueError(f"time has no timezone: {value!r}")
    text = moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    if moment.microsecond:
        text = f"{text}.{moment.microsecond:06d}"
    return f"{text}Z"


def _required_text(payload: Mapping[str, object], name: str, event: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"webhook v1 {event} body needs {name}")
    return value


def _optional_time(payload: Mapping[str, object], name: str) -> str | None:
    value = payload.get(name)
    return None if value is None else _utc_text(value)


def _event_time(payload: Mapping[str, object], change: AlertTransition, event: str) -> str:
    name = _TIME_KEY_BY_TRANSITION[change]
    value = payload.get(name)
    if value is None:
        raise ValueError(f"webhook v1 {event} body needs {name}")
    return _utc_text(value)


def _source_id(payload: Mapping[str, object]) -> str | None:
    """The bare UUID of the stored source id; ``None`` when the alert has none."""
    value = payload.get("source_id")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"webhook v1 source_id is not text: {value!r}")
    text = value.removeprefix(SOURCE_ID_PREFIX)
    try:
        return str(uuid.UUID(text))
    except ValueError as exc:
        raise ValueError(f"webhook v1 source_id is not a UUID: {value!r}") from exc


def _principal(payload: Mapping[str, object], name: str, event: str) -> str | None:
    """The actor of an acknowledged/resolved event, unprefixed."""
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"webhook v1 {event} body has a malformed {name}: {value!r}")
    text = value.removeprefix(PRINCIPAL_PREFIX)
    if not text:
        raise ValueError(f"webhook v1 {event} body has an empty {name}")
    return text


def _occurrence_count(payload: Mapping[str, object]) -> int | None:
    """The alert's occurrence count as stored; ``None`` when it is not there.

    A missing count is null in the body, never a fabricated 0.
    """
    fields = payload.get("fields")
    if not isinstance(fields, Mapping):
        return None
    value = fields.get("occurrences")
    return value if isinstance(value, int) else None


def _encode(body: dict[str, object]) -> bytes:
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _headers(idempotency_key: str) -> dict[str, str]:
    return {"Content-Type": CONTENT_TYPE, "Idempotency-Key": idempotency_key}


def build_webhook_v1(
    payload: dict,
    *,
    alert_id: uuid.UUID | str,
    transition: AlertTransition | str,
    destination_id: str,
    public_origin: str,
) -> tuple[bytes, dict[str, str]]:
    """One lifecycle event's JSON body and headers (design §4).

    ``payload`` is the frozen ``alert_notification.payload``. ``alert_id`` comes from
    the notification column, because the stored copy is redacted as token-shaped, the
    same reason ``_message_from_payload`` reads it there. A body that cannot meet the
    contract (no transition time, no severity or status) raises ``ValueError`` rather
    than sending a field invented at delivery time.
    """
    change = AlertTransition(transition)
    event = _EVENT_BY_TRANSITION[change]
    identifier = str(alert_id)
    key = f"{identifier}:{change.value}:{destination_id}"
    url = f"{public_origin.rstrip('/')}/alerts/{identifier}"

    family = payload.get("family")
    source_type = _required_text(payload, "alert_type", event)
    severity = Severity(_required_text(payload, "severity", event)).value
    subject = _required_text(payload, "title", event)
    status = AlertStatus(_required_text(payload, "status", event)).value

    if change is AlertTransition.OPENED:
        opened_at = _event_time(payload, change, event)
        body: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "event": event,
            "idempotency_key": key,
            "alert_id": identifier,
            "fingerprint": payload.get("fingerprint"),
            "family": family,
            "source_type": source_type,
            "source_id": _source_id(payload),
            "severity": severity,
            "subject": subject,
            "summary": payload.get("body"),
            "status": status,
            "occurrence_count": _occurrence_count(payload),
            "opened_at": opened_at,
            "occurred_at": opened_at,
            "url": url,
            "honesty": None,
        }
    elif change is AlertTransition.ACKNOWLEDGED:
        acknowledged_at = _event_time(payload, change, event)
        body = {
            "schema_version": SCHEMA_VERSION,
            "event": event,
            "idempotency_key": key,
            "alert_id": identifier,
            "family": family,
            "source_type": source_type,
            "severity": severity,
            "subject": subject,
            "status": status,
            "opened_at": _optional_time(payload, "opened_at"),
            "acknowledged_at": acknowledged_at,
            "acknowledged_by": _principal(payload, "acknowledged_by", event),
            "occurred_at": acknowledged_at,
            "url": url,
            "honesty": None,
        }
    else:
        resolved_at = _event_time(payload, change, event)
        body = {
            "schema_version": SCHEMA_VERSION,
            "event": event,
            "idempotency_key": key,
            "alert_id": identifier,
            "family": family,
            "source_type": source_type,
            "severity": severity,
            "subject": subject,
            "status": status,
            "resolved_at": resolved_at,
            "resolved_by": _principal(payload, "resolved_by", event),
            # The code only: a resolution's free text never leaves the site.
            "resolution_reason_code": payload.get("resolution_reason_code"),
            "occurred_at": resolved_at,
            "url": url,
            "honesty": None,
        }
    return _encode(body), _headers(key)


def build_webhook_v1_test(destination_id: str, now: datetime) -> tuple[bytes, dict[str, str]]:
    """The ``test`` event a destination's "send test" posts (design §4).

    It has no alert: every alert-derived field is null. The idempotency key carries
    ``now`` so two tests of one destination are two events a peer may log, not a
    retry it drops.
    """
    occurred_at = _utc_text(now)
    key = f"test:{destination_id}:{occurred_at}"
    body: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "event": "test",
        "idempotency_key": key,
        "alert_id": None,
        "family": None,
        "severity": Severity.LOW.value,
        "subject": "Notification test",
        "occurred_at": occurred_at,
        "url": None,
        "honesty": None,
    }
    return _encode(body), _headers(key)


def sign_v1(body: bytes, secret: str) -> str:
    """The ``X-QRadar-Dash-Signature`` value for ``body``: ``v1=<hex>``.

    Pure: the secret is a parameter and is never logged. The peer recomputes it over
    the raw bytes it received, so the body must be sent exactly as built.
    """
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"v1={digest}"
