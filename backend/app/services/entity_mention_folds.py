"""Pure folds from stored parent values to entity timeline index rows."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, cast

from app.services.entity_mentions import (
    EntityKind,
    EntityMentionRow,
    EntityRole,
    classify_entity,
    normalize_entity,
    user_match_forms,
)

_EXPLANATION_DIMENSIONS: dict[str, tuple[EntityKind, EntityRole]] = {
    "source_ip": ("IP", "SOURCE_IP"),
    "destination_ip": ("IP", "DESTINATION_IP"),
    "username": ("USER", "USER"),
}
_DESTINATION_OFFENSE_TYPES = (1, 11)
_PW_PROPERTY_KINDS: dict[str, tuple[EntityKind, EntityRole]] = {
    "username": ("USER", "USER"),
    "sourceip": ("IP", "SOURCE_IP"),
    "destinationip": ("IP", "DESTINATION_IP"),
}


def explanation_mentions(
    instance_id: uuid.UUID,
    contributors: Sequence[Mapping[str, object]],
    *,
    anomaly_window_start: datetime,
    anomaly_window_end: datetime,
) -> list[EntityMentionRow]:
    """Fold stored explanation contributor ids, dimensions and values."""
    mentions: list[EntityMentionRow] = []
    for contributor in contributors:
        target = _EXPLANATION_DIMENSIONS.get(str(contributor.get("dimension") or ""))
        raw = contributor.get("value")
        if target is None or not isinstance(raw, str):
            continue
        kind, role = target
        values = user_match_forms(raw)[-1:] if kind == "USER" else (normalize_entity(kind, raw),)
        for value in values:
            if value is None:
                continue
            mentions.append(
                EntityMentionRow(
                    instance_id=instance_id,
                    kind=kind,
                    value_norm=value,
                    role=role,
                    source="EXPLANATION",
                    ref_table="anomaly_explanation_contributor",
                    ref_id=str(contributor["id"]),
                    occurred_at=anomaly_window_start,
                    occurred_end=anomaly_window_end,
                )
            )
    return mentions


def waf_top_mentions(
    instance_id: uuid.UUID,
    *,
    waf_endpoint_id: object,
    bucket_start: datetime,
    top_clients: Sequence[Mapping[str, object]] | None,
) -> list[EntityMentionRow]:
    """Fold one stored WAF endpoint metric's bounded top-client list."""
    mentions: list[EntityMentionRow] = []
    for client in top_clients or ():
        value = normalize_entity("IP", str(client.get("source_ip") or ""))
        if value is None:
            continue
        mentions.append(
            EntityMentionRow(
                instance_id=instance_id,
                kind="IP",
                value_norm=value,
                role="SOURCE_IP",
                source="WAF_TOP",
                ref_table="waf_endpoint_metric",
                ref_id=str(waf_endpoint_id),
                occurred_at=bucket_start,
            )
        )
    return mentions


def ips_top_mentions(
    instance_id: uuid.UUID,
    *,
    log_source_id: object,
    bucket_start: datetime,
    contributors: Mapping[str, Sequence[Mapping[str, object]]],
) -> list[EntityMentionRow]:
    """Fold one stored IPS source bucket's bounded source-IP contributor lists."""
    mentions: list[EntityMentionRow] = []
    for dimension in ("SOURCE_IP", "PORT_FANOUT"):
        for contributor in contributors.get(dimension, ()):
            value = normalize_entity("IP", str(contributor.get("source_ip") or ""))
            if value is None:
                continue
            mentions.append(
                EntityMentionRow(
                    instance_id=instance_id,
                    kind="IP",
                    value_norm=value,
                    role="SOURCE_IP",
                    source="IPS_TOP",
                    ref_table="ips_source_bucket",
                    ref_id=str(log_source_id),
                    occurred_at=bucket_start,
                )
            )
    return mentions


def ips_entity_mentions(
    instance_id: uuid.UUID,
    *,
    ips_entity_id: object,
    bucket_start: datetime,
    value_key: str,
) -> list[EntityMentionRow]:
    """Fold the stored destination-IP entity joined to one stored metric bucket."""
    value = normalize_entity("IP", value_key)
    if value is None:
        return []
    return [
        EntityMentionRow(
            instance_id=instance_id,
            kind="IP",
            value_norm=value,
            role="DESTINATION_IP",
            source="IPS_ENTITY",
            ref_table="ips_entity",
            ref_id=str(ips_entity_id),
            occurred_at=bucket_start,
        )
    ]


def offense_mentions(
    instance_id: uuid.UUID,
    *,
    qradar_offense_id: int,
    captured_at: datetime,
    usernames: Sequence[str | None],
    offense_source: str | None,
    offense_type: int | None,
    start_time: datetime | None,
    last_updated_time: datetime | None,
    close_time: datetime | None,
) -> list[EntityMentionRow]:
    """Fold the stored columns of one offense snapshot."""
    occurred_at = start_time or last_updated_time or captured_at
    occurred_end = close_time or last_updated_time
    if occurred_end is not None and occurred_end <= occurred_at:
        occurred_end = None

    def row(kind: EntityKind, value: str, role: EntityRole) -> EntityMentionRow | None:
        value_norm = normalize_entity(kind, value)
        if value_norm is None:
            return None
        return EntityMentionRow(
            instance_id=instance_id,
            kind=kind,
            value_norm=value_norm,
            role=role,
            source="OFFENSE",
            ref_table="offense_snapshot",
            ref_id=str(qradar_offense_id),
            occurred_at=occurred_at,
            occurred_end=occurred_end,
        )

    rows = [
        row("USER", form, "USER")
        for username in usernames
        for form in user_match_forms(username or "")[-1:]
    ]
    if offense_source and classify_entity(offense_source) == "IP":
        destination = offense_type in _DESTINATION_OFFENSE_TYPES
        rows.append(
            row("IP", offense_source, "DESTINATION_IP" if destination else "SOURCE_IP")
        )
    return [item for item in rows if item is not None]


def log_source_mentions(
    instance_id: uuid.UUID,
    sources: Sequence[Mapping[str, object]],
    *,
    observed_at: datetime,
) -> list[EntityMentionRow]:
    """Fold the stored id, name and identifier of synchronized log sources."""
    mentions: list[EntityMentionRow] = []
    for source in sources:
        name = source.get("name")
        identifier = source.get("identifier")
        candidates: list[tuple[EntityKind, object]] = [("HOST", name), ("HOST", identifier)]
        if isinstance(identifier, str) and classify_entity(identifier) == "IP":
            candidates.append(("IP", identifier))
        for kind, raw in candidates:
            value = normalize_entity(kind, raw) if isinstance(raw, str) else None
            if value is None:
                continue
            mentions.append(
                EntityMentionRow(
                    instance_id=instance_id,
                    kind=kind,
                    value_norm=value,
                    role="HOST",
                    source="LOG_SOURCE",
                    ref_table="log_source",
                    ref_id=str(source["id"]),
                    occurred_at=observed_at,
                )
            )
    return mentions


def catchall_mentions(
    instance_id: uuid.UUID, buckets: Sequence[Mapping[str, Any]]
) -> list[EntityMentionRow]:
    """Fold the bounded sender values persisted in catch-all bucket rows."""
    mentions: list[EntityMentionRow] = []
    for bucket in buckets:
        occurred_at = cast(datetime, bucket["bucket_start"])
        occurred_end = occurred_at + timedelta(seconds=cast(int, bucket["bucket_seconds"]))
        for sender in cast(Sequence[Mapping[str, Any]], bucket.get("top_senders") or ()):
            raw = str(sender.get("identifier") or "")
            kind = classify_entity(raw) or "HOST"
            value = normalize_entity(kind, raw)
            if value is None:
                continue
            mentions.append(
                EntityMentionRow(
                    instance_id=instance_id,
                    kind=kind,
                    value_norm=value,
                    role="SOURCE_IP" if kind == "IP" else "HOST",
                    source="CATCHALL",
                    ref_table="catchall_bucket",
                    ref_id=str(bucket["catchall_log_source_id"]),
                    occurred_at=occurred_at,
                    occurred_end=occurred_end,
                )
            )
    return mentions


def identity_top_mentions(
    instance_id: uuid.UUID,
    *,
    log_source_id: uuid.UUID,
    event_id: int,
    bucket_start: datetime,
    bucket_end: datetime | None,
    usernames: Sequence[Any],
) -> list[EntityMentionRow]:
    """Fold one stored identity top-N list. One USER mention per stored name.

    The caller passes only rows the writer stored. A name the fold kept inside
    the admission floor is not in that list, so it cannot become a mention.
    Normalization is ``normalize_entity("USER")``; the table keeps QRadar's string.
    """
    mentions: list[EntityMentionRow] = []
    ref_id = f"{log_source_id}:{event_id}"
    for stored in usernames:
        raw = getattr(stored, "username", None)
        if not isinstance(raw, str):
            continue
        value = normalize_entity("USER", raw)
        if value is None:
            continue
        mentions.append(
            EntityMentionRow(
                instance_id=instance_id,
                kind="USER",
                value_norm=value,
                role="USER",
                source="IDENTITY_TOP",
                ref_table="identity_top_username",
                ref_id=ref_id,
                occurred_at=bucket_start,
                occurred_end=bucket_end,
            )
        )
    return mentions


def pw_value_mentions(
    instance_id: uuid.UUID,
    properties: Sequence[Mapping[str, str]],
    *,
    pw_value_id: uuid.UUID,
    value_key: str,
    truncated: bool,
    bucket_start: datetime,
    bucket_seconds: int,
) -> list[EntityMentionRow]:
    """Fold one stored property-watch value and metric bucket."""
    if len(properties) != 1 or truncated:
        return []
    name = str(properties[0].get("name") or "").strip().lower()
    classified = _PW_PROPERTY_KINDS.get(name)
    if classified is None:
        return []
    kind, role = classified
    if kind == "IP" and classify_entity(value_key) != "IP":
        return []
    if kind == "USER":
        forms = user_match_forms(value_key)
        normalized = forms[-1] if forms else None
    else:
        normalized = normalize_entity(kind, value_key)
    if normalized is None:
        return []
    return [
        EntityMentionRow(
            instance_id=instance_id,
            kind=kind,
            value_norm=normalized,
            role=role,
            source="PW_VALUE",
            ref_table="pw_value_metric",
            ref_id=str(pw_value_id),
            occurred_at=bucket_start,
            occurred_end=bucket_start + timedelta(seconds=bucket_seconds),
        )
    ]
