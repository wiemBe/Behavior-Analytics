"""Apply one JDBC parameter walk to the safe per-source snapshots (design §3).

Apply only, no commit: the caller owns the transaction. The sole input is the
containable J1a/J1b walk. Fail closed: unmatched/protocol-changed configs are
not written; a complete walk with zero rows is unproven (D-95 N1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Insert

from app.jdbc.dto import ConfigState, FieldStateReason, JdbcConfigDTO, JdbcConfigWalk
from app.models.jdbc import JdbcSourceConfig, validate_field_states
from app.models.log_source import LogSource

#: The 18 safe members (design §2), so every write sets every column explicitly.
SAFE_MEMBERS = (
    "database_type database_host tls_hostname database_port database_name schema_name "  # noqa: SIM905
    "table_name select_list compare_field polling_interval_seconds start_time_local "
    "prepared_statements predefined_query eps_throttle source_label use_ssl "
    "allow_untrusted advanced_options".split()
)

FAILURE_PARAMETERS_UNAVAILABLE = "parameters_unavailable"
FAILURE_ABSENT = "absent_from_parameter_snapshot"

CONFIG = JdbcSourceConfig.__table__
CHUNK = 500


@dataclass(frozen=True)
class JdbcApplySummary:
    """Counts and one honesty flag only. No value, name, host or id is carried."""

    written: int = 0
    unmatched: int = 0
    skipped_protocol: int = 0
    cleared: int = 0
    not_stored_unverified: int = 0
    marked_absent: int = 0
    empty_unproven: bool = False
    chunks: int = 0


def _chunks[T](rows: list[T]) -> list[list[T]]:
    return [rows[i : i + CHUNK] for i in range(0, len(rows), CHUNK)]


def _stored_states(dto: JdbcConfigDTO) -> dict[str, Any]:
    """Translate J1a states to stored states, then validate explicitly.

    J1a marks a name absent from a valid array UNAVAILABLE/missing; storage
    spells that terminal state REMOVED (design §3). Others are kept as reported.
    """
    raw: dict[str, Any] = {}
    for member, state in dto.field_states.items():
        code = state.code.value
        reason = state.reason.value if state.reason is not None else None
        if reason == FieldStateReason.MISSING.value:
            code = "REMOVED"
            reason = None
        raw[member] = {"code": code, "reason": reason}
    return validate_field_states(raw)


def _full_payload(
    lsid: UUID, dto: JdbcConfigDTO, now: datetime, previous: dict[str, Any] | None
) -> dict[str, Any]:
    """A whole-snapshot replacement; every column is set."""
    row: dict[str, Any] = {"log_source_id": lsid, "protocol_type_id": 8}
    for member in SAFE_MEMBERS:
        row[member] = getattr(dto, member)
    row.update(
        field_states=_stored_states(dto),
        allowlist_version=dto.allowlist_version,
        config_state=dto.config_state.value,
        failure_code=None,
        observed_at=now,
        last_success_at=(
            now
            if dto.config_state is ConfigState.COMPLETE
            else (previous["last_success_at"] if previous is not None else None)
        ),
    )
    return row


def _null_payload(lsid: UUID, dto: JdbcConfigDTO, now: datetime) -> dict[str, Any]:
    """A NOT_COLLECTED source with no previous row: nulls, never a guessed default."""
    row: dict[str, Any] = {"log_source_id": lsid, "protocol_type_id": 8}
    for member in SAFE_MEMBERS:
        row[member] = None
    row.update(
        field_states=validate_field_states({}),
        allowlist_version=dto.allowlist_version,
        config_state=ConfigState.NOT_COLLECTED.value,
        failure_code=FAILURE_PARAMETERS_UNAVAILABLE,
        observed_at=now,
        last_success_at=None,
    )
    return row


def _keep_payload(previous: dict[str, Any], code: str) -> dict[str, Any]:
    """Keep values, field states and both timestamps; change only the read state."""
    row = dict(previous)
    row["config_state"] = ConfigState.NOT_COLLECTED.value
    row["failure_code"] = code
    return row


def _upsert_all() -> Insert:
    stmt = pg_insert(cast(Table, CONFIG))
    excluded = stmt.excluded
    set_ = {
        column.name: excluded[column.name]
        for column in CONFIG.columns
        if column.name != "log_source_id"
    }
    return stmt.on_conflict_do_update(index_elements=["log_source_id"], set_=set_)


def _upsert_state_only() -> Insert:
    stmt = pg_insert(cast(Table, CONFIG))
    return stmt.on_conflict_do_update(
        index_elements=["log_source_id"],
        set_={
            "config_state": stmt.excluded.config_state,
            "failure_code": stmt.excluded.failure_code,
        },
    )


async def apply_jdbc_walk(
    session: AsyncSession,
    instance_id: UUID,
    walk: JdbcConfigWalk,
    *,
    now: datetime,
) -> JdbcApplySummary:
    """Write one walk's safe snapshots in the caller's session; do not commit."""
    cleared = written = unmatched = skipped = not_stored = marked_absent = chunks = 0
    empty_unproven = False

    # One inventory read and one existing-snapshot read for the whole console.
    inventory_rows = (
        await session.execute(
            select(LogSource.qradar_id, LogSource.id, LogSource.protocol_type_id).where(
                LogSource.instance_id == instance_id
            )
        )
    ).all()
    inventory: dict[int, tuple[UUID, int | None]] = {
        qradar_id: (lsid, protocol) for qradar_id, lsid, protocol in inventory_rows
    }

    existing_rows = (
        await session.execute(
            select(JdbcSourceConfig)
            .join(LogSource, JdbcSourceConfig.log_source_id == LogSource.id)
            .where(LogSource.instance_id == instance_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    existing: dict[UUID, dict[str, Any]] = {}
    for (config,) in existing_rows:
        existing[config.log_source_id] = {
            column.name: getattr(config, column.name) for column in CONFIG.columns
        }

    configs_by_qid: dict[int, JdbcConfigDTO] = {}
    for dto in walk.configs:
        configs_by_qid[dto.qradar_id] = dto

    full_rows: list[dict[str, Any]] = []
    keep_rows: list[dict[str, Any]] = []
    seen_qids: set[int] = set()
    for qradar_id, dto in configs_by_qid.items():
        seen_qids.add(qradar_id)
        if dto.config_state in (ConfigState.UNVERIFIED_PROTOCOL, ConfigState.NOT_JDBC):
            not_stored += 1
            continue
        located = inventory.get(qradar_id)
        if located is None:
            unmatched += 1
            continue
        lsid, protocol = located
        if protocol != 8:
            skipped += 1
            continue
        previous = existing.get(lsid)
        if dto.config_state in (ConfigState.COMPLETE, ConfigState.PARTIAL):
            full_rows.append(_full_payload(lsid, dto, now, previous))
        elif dto.config_state is ConfigState.NOT_COLLECTED:
            if previous is not None:
                keep_rows.append(_keep_payload(previous, FAILURE_PARAMETERS_UNAVAILABLE))
            else:
                full_rows.append(_null_payload(lsid, dto, now))
        written += 1

    full_statement = _upsert_all()
    keep_statement = _upsert_state_only()
    for rows, statement in ((full_rows, full_statement), (keep_rows, keep_statement)):
        for chunk in _chunks(rows):
            await session.execute(statement, chunk)
            chunks += 1

    if walk.complete:
        known_jdbc = {q: lsid for q, (lsid, protocol) in inventory.items() if protocol == 8}
        if not walk.configs:
            # A complete walk with no rows is not proof of absence (D-95 N1).
            empty_unproven = bool(known_jdbc)
        else:
            absent_rows = [
                _keep_payload(existing[lsid], FAILURE_ABSENT)
                for q, lsid in known_jdbc.items()
                if q not in seen_qids and lsid in existing
            ]
            for chunk in _chunks(absent_rows):
                await session.execute(keep_statement, chunk)
                chunks += 1
            marked_absent = len(absent_rows)

    # One instance-scoped DELETE after every write rechecks the protocol in
    # SQL: a change committed after the reads still wins, with no per-source
    # statement (design §3).
    non_jdbc = select(LogSource.id).where(
        LogSource.instance_id == instance_id,
        LogSource.protocol_type_id.isnot(None),
        LogSource.protocol_type_id != 8,
    )
    result = cast(
        CursorResult[Any],
        await session.execute(
            delete(JdbcSourceConfig).where(JdbcSourceConfig.log_source_id.in_(non_jdbc))
        ),
    )
    cleared = result.rowcount

    return JdbcApplySummary(
        written=written,
        unmatched=unmatched,
        skipped_protocol=skipped,
        cleared=cleared,
        not_stored_unverified=not_stored,
        marked_absent=marked_absent,
        empty_unproven=empty_unproven,
        chunks=chunks,
    )
