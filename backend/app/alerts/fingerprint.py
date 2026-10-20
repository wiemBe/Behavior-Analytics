"""Deterministic alert fingerprints, computed at the application layer.

The fingerprint is the identity of an alertable *condition* — not of a single
detection. Two detections of "log source X has VOLUME_DROP" must produce the
same fingerprint so the second updates the open alert instead of opening a new
one.

Layering:
  * This module is the primary dedup mechanism: the service looks up the open
    alert by fingerprint before deciding to open vs. update.
  * The partial unique index on ``alert(dedup_key) WHERE status <> 'RESOLVED'``
    (see app/models/alert.py) is the *final* concurrency safeguard, catching the
    race where two workers compute the same fingerprint simultaneously. The DB
    constraint should essentially never fire; if it does, the service treats the
    IntegrityError as "someone else opened it" and reloads.

Determinism rules:
  * Only stable identifiers go into the hash — never timestamps, counts, or
    observed values, which change every interval.
  * Component order is fixed and values are normalised (lower-cased, stripped)
    so the same logical condition always hashes identically.
"""

from __future__ import annotations

import hashlib
import json
import uuid


def _normalise(value: object) -> str:
    return str(value).strip().lower()


def compute_fingerprint(*, source_type: str, source_id: str | uuid.UUID, condition: str,
                        subtype: str | None = None) -> str:
    """Stable fingerprint for an alertable condition.

    Args:
        source_type: e.g. "log_source", "scheduled_search".
        source_id:   the stable id of that object.
        condition:   the condition family, e.g. "anomaly", "search_threshold".
        subtype:     optional discriminator, e.g. the anomaly type "VOLUME_DROP".
    """
    parts = [_normalise(source_type), _normalise(source_id), _normalise(condition)]
    if subtype is not None:
        parts.append(_normalise(subtype))
    joined = "|".join(parts)
    digest = hashlib.sha256(joined.encode()).hexdigest()[:32]
    return f"{condition}:{digest}"


def anomaly_fingerprint(log_source_id: str | uuid.UUID, anomaly_type: str) -> str:
    return compute_fingerprint(
        source_type="log_source",
        source_id=log_source_id,
        condition="anomaly",
        subtype=anomaly_type,
    )


def inventory_fingerprint(
    instance_id: str | uuid.UUID, qradar_log_source_id: int
) -> str:
    """Identity of one QRadar source first appearing on one instance."""
    return compute_fingerprint(
        source_type="log_source",
        source_id=f"{instance_id}:{qradar_log_source_id}",
        condition="inventory",
        subtype="NEW_LOG_SOURCE",
    )


def catchall_sender_fingerprint(
    instance_id: str | uuid.UUID,
    catchall_log_source_id: str | uuid.UUID,
    identifier: str,
) -> str:
    """Identity of one sender first appearing on one catch-all source (C.8).

    The identifier is hashed separately because `compute_fingerprint` lower-cases
    its parts, and two senders that differ only by case are two senders.
    """
    return compute_fingerprint(
        source_type="catchall_sender",
        source_id=(
            f"{instance_id}:{catchall_log_source_id}:"
            f"{hashlib.sha256(identifier.encode()).hexdigest()}"
        ),
        condition="inventory",
        subtype="NEW_CATCHALL_SENDER",
    )


def waf_anomaly_fingerprint(
    *,
    instance_id: str | uuid.UUID,
    log_source_id: str | uuid.UUID,
    virtual_host: str | None,
    normalized_endpoint: str,
    http_method: str,
    normalizer_version: int,
    detector_type: str,
) -> str:
    """Identity of one WAF detector condition on an endpoint natural key."""
    natural_key = json.dumps(
        [
            str(instance_id),
            str(log_source_id),
            virtual_host,
            normalized_endpoint,
            http_method,
            normalizer_version,
        ],
        separators=(",", ":"),
    )
    return compute_fingerprint(
        source_type="waf_endpoint",
        source_id=natural_key,
        condition="waf_anomaly",
        subtype=detector_type,
    )


def ips_anomaly_fingerprint(
    *,
    instance_id: str | uuid.UUID,
    log_source_id: str | uuid.UUID,
    detector_type: str,
    dimension: str | None = None,
    value_key: str | None = None,
) -> str:
    """Identity of one IPS detector condition on its stable subject key."""
    if (dimension is None) != (value_key is None):
        raise ValueError("dimension and value_key must both identify an entity, or both be absent")
    natural_key = [str(instance_id), str(log_source_id)]
    if dimension is not None and value_key is not None:
        natural_key.extend((dimension, value_key))
    return compute_fingerprint(
        source_type="ips_entity" if dimension is not None else "ips_source",
        source_id=json.dumps(natural_key, separators=(",", ":")),
        condition="ips_anomaly",
        subtype=detector_type,
    )


def pw_anomaly_fingerprint(
    instance_id: str | uuid.UUID,
    watch_id: str | uuid.UUID,
    detector_type: str,
    value_key: str,
) -> str:
    """Identity of one property-watch detector condition on one value (design §G.10.1).

    The natural key is ``(instance, watch, value_key)``, never a row id, so an incident
    survives its value being retired and reactivated. ``watch_version`` is deliberately not
    in it: a version bump resolves the watch's alerts instead. The value text enters only as
    its SHA-256 (hashed here, before ``compute_fingerprint`` lower-cases its parts, because
    two values that differ only by case are two values), so the fingerprint, which is logged
    and stored on the alert, never carries it.
    """
    if not value_key:
        raise ValueError("value_key is required")
    natural_key = json.dumps(
        [str(instance_id), str(watch_id), hashlib.sha256(value_key.encode()).hexdigest()],
        separators=(",", ":"),
    )
    return compute_fingerprint(
        source_type="pw_value",
        source_id=natural_key,
        condition="pw_anomaly",
        subtype=detector_type,
    )


def search_threshold_fingerprint(search_id: str | uuid.UUID) -> str:
    return compute_fingerprint(
        source_type="scheduled_search",
        source_id=search_id,
        condition="search_threshold",
    )


def custom_rule_breach_fingerprint(rule_id: str | uuid.UUID) -> str:
    """Identity of one custom rule's threshold condition (design §F.6).

    ``source_type`` and ``condition`` are a new kind, not a member of the
    anomaly, WAF, IPS or search families, so a rule cannot deduplicate into
    one of those alerts. The condition string is ``custom_rule_breach`` — not
    ``custom_rule`` — so the fingerprint prefix cannot collide with a future
    custom-rule condition that is not a breach.
    """
    return compute_fingerprint(
        source_type="custom_rule",
        source_id=rule_id,
        condition="custom_rule_breach",
    )


def search_failure_fingerprint(search_id: str | uuid.UUID) -> str:
    """Identity of "this scheduled search keeps failing to run".

    Deliberately distinct from the threshold condition: a search that errors and
    a search whose result crossed its threshold are different problems with
    different owners, and must not deduplicate into one another.
    """
    return compute_fingerprint(
        source_type="scheduled_search",
        source_id=search_id,
        condition="search_failure",
    )
