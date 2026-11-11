"""Normalization and bounded bulk persistence for the entity timeline index."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ips.fold import canonical_ip
from app.models.entity import EntityMention

type EntityKind = Literal["IP", "HOST", "USER"]
type EntityRole = Literal["SOURCE_IP", "DESTINATION_IP", "HOST", "USER"]
type EntitySource = Literal[
    "EXPLANATION",
    "WAF_TOP",
    "IPS_TOP",
    "IPS_ENTITY",
    "OFFENSE",
    "CATCHALL",
    "PW_VALUE",
    "LOG_SOURCE",
]

MAX_ENTITY_VALUE_LENGTH = 255
MENTION_INSERT_CHUNK_SIZE = 1000


@dataclass(frozen=True)
class EntityMentionRow:
    instance_id: uuid.UUID
    kind: EntityKind
    value_norm: str
    role: EntityRole
    source: EntitySource
    ref_table: str
    ref_id: str
    occurred_at: datetime
    occurred_end: datetime | None = None


def normalize_entity(kind: str, raw: str) -> str | None:
    """Return one canonical entity value, or None when it must not be recorded."""
    if kind == "IP":
        return canonical_ip(raw)

    value = raw.strip().lower()
    if kind == "HOST" and value.endswith("."):
        value = value[:-1]
    if kind not in ("HOST", "USER"):
        return None
    if not value or len(value) > MAX_ENTITY_VALUE_LENGTH:
        return None
    return value


def user_match_forms(raw: str) -> tuple[str, ...]:
    """Return the stored user plus its account form, without rewriting the stored value."""
    full = normalize_entity("USER", raw)
    if full is None:
        return ()

    account = full.rsplit("\\", 1)[-1]
    if "@" in account:
        account = account.split("@", 1)[0]
    if not account or account == full:
        return (full,)
    return full, account


def classify_entity(raw: str) -> EntityKind | None:
    """Classify addresses only; host and user text requires caller-supplied context."""
    return "IP" if canonical_ip(raw) is not None else None


def _mention_key(row: EntityMentionRow) -> tuple[object, ...]:
    return (
        row.instance_id,
        row.kind,
        row.value_norm,
        row.source,
        row.ref_table,
        row.ref_id,
        row.occurred_at,
    )


async def record_mentions(
    session: AsyncSession,
    mentions: Iterable[EntityMentionRow],
    *,
    single_statement: bool = False,
) -> None:
    """Insert normalized mentions in fixed-size chunks without committing.

    The database identity omits role and end time, so the first row for a primary-key
    identity wins during in-memory de-duplication. Values over 255 characters are skipped,
    never truncated into a different entity.
    """
    unique: dict[tuple[object, ...], EntityMentionRow] = {}
    for mention in mentions:
        if not mention.value_norm or len(mention.value_norm) > MAX_ENTITY_VALUE_LENGTH:
            continue
        unique.setdefault(_mention_key(mention), mention)

    rows = list(unique.values())
    if single_statement and rows:
        payload = json.dumps(
            [
                {
                    "instance_id": str(row.instance_id),
                    "kind": row.kind,
                    "value_norm": row.value_norm,
                    "role": row.role,
                    "source": row.source,
                    "ref_table": row.ref_table,
                    "ref_id": row.ref_id,
                    "occurred_at": row.occurred_at.isoformat(),
                    "occurred_end": (
                        row.occurred_end.isoformat() if row.occurred_end is not None else None
                    ),
                }
                for row in rows
            ]
        )
        await session.execute(
            text(
                """
                INSERT INTO entity_mention (
                    instance_id, kind, value_norm, role, source, ref_table, ref_id,
                    occurred_at, occurred_end
                )
                SELECT
                    incoming.instance_id, incoming.kind, incoming.value_norm, incoming.role,
                    incoming.source, incoming.ref_table, incoming.ref_id, incoming.occurred_at,
                    incoming.occurred_end
                FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS incoming(
                    instance_id uuid,
                    kind text,
                    value_norm text,
                    role text,
                    source text,
                    ref_table text,
                    ref_id text,
                    occurred_at timestamptz,
                    occurred_end timestamptz
                )
                ON CONFLICT ON CONSTRAINT pk_entity_mention DO NOTHING
                """
            ),
            {"rows": payload},
        )
        return

    for offset in range(0, len(rows), MENTION_INSERT_CHUNK_SIZE):
        chunk = rows[offset : offset + MENTION_INSERT_CHUNK_SIZE]
        statement = (
            pg_insert(EntityMention)
            .values(
                [
                    {
                        "instance_id": row.instance_id,
                        "kind": row.kind,
                        "value_norm": row.value_norm,
                        "role": row.role,
                        "source": row.source,
                        "ref_table": row.ref_table,
                        "ref_id": row.ref_id,
                        "occurred_at": row.occurred_at,
                        "occurred_end": row.occurred_end,
                    }
                    for row in chunk
                ]
            )
            .on_conflict_do_nothing(constraint="pk_entity_mention")
        )
        await session.execute(statement)


async def delete_mentions_for(
    session: AsyncSession, ref_table: str, ref_ids: Iterable[str]
) -> None:
    """Delete mentions for a parent batch in one statement without committing."""
    parent_ids = tuple(dict.fromkeys(ref_ids))
    if not parent_ids:
        return
    await session.execute(
        delete(EntityMention).where(
            EntityMention.ref_table == ref_table,
            EntityMention.ref_id.in_(parent_ids),
        )
    )
