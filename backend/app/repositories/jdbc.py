"""JDBC list and detail in four data SELECTs (design §6): count, page, last observations,
run state. Composes `app.jdbc.queries`; flow state and measurement come from the SQL CASE and
are only labelled here, never re-derived. No route, provider or QRadar call.
"""

from __future__ import annotations

import builtins
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, get_args

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.jdbc import flow, queries
from app.jdbc.dto import ConfigState, FieldStateReason
from app.jdbc.flow import AttemptState
from app.jdbc.parameters import FIELDS, UNVERIFIED, Bad
from app.jdbc.queries import JDBC_LIKE_PROTOCOLS, JdbcFilters
from app.log_source_policy import source_pause_enabled
from app.models.log_source import LogSource
from app.schemas import jdbc as out

logger = logging.getLogger(__name__)

DEFAULT_LIMIT, MAX_QUERY_CHARS = 100, 200
_FLOW_STATES = frozenset(get_args(flow.FlowState))
_WORST: tuple[AttemptState, ...] = ("FAILED", "PARTIAL", "NOT_COLLECTED", "COMPLETE")
_MEASURED_REASON: dict[str, flow.ReasonCode] = {
    "EXACT": "measured", "PARTIAL": "partial", "TRUNCATED": "truncated",
    "NOT_COLLECTED": "not_collected",
}


class JdbcQueryError(ValueError):
    """A rejected list parameter; the message is fixed and never echoes the value."""


@dataclass(frozen=True)
class JdbcSnapshot:
    """The console inventory passes behind `total`: it counts stored rows, never QRadar's."""

    complete: bool
    state: AttemptState
    stale: bool
    last_complete_at: datetime | None
    consoles: int


@dataclass(frozen=True)
class JdbcPage:
    items: builtins.list[out.JdbcSourceRow]
    total: int
    limit: int
    offset: int
    snapshot: JdbcSnapshot


class JdbcRepository:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    def bounds(
        self, *, limit: int | None = None, offset: int = 0, q: str | None = None,
        enabled: bool | None = None, protocol: int | None = None,
        instance_id: uuid.UUID | None = None, flow_state: str | None = None,
        include_absent: bool = False,
    ) -> tuple[int, int, JdbcFilters]:
        """Validate and normalise list parameters before any SQL is sent (route reuses it)."""
        if type(offset) is not int or offset < 0:
            raise JdbcQueryError("offset_invalid")
        needle = (q or "").strip()
        if (limit is not None and type(limit) is not int) or len(needle) > MAX_QUERY_CHARS:
            raise JdbcQueryError("limit_or_q_invalid")
        if enabled is not None and type(enabled) is not bool:
            raise JdbcQueryError("enabled_invalid")
        if protocol is not None and protocol not in JDBC_LIKE_PROTOCOLS:
            raise JdbcQueryError("protocol_unknown")
        if instance_id is not None and not isinstance(instance_id, uuid.UUID):
            raise JdbcQueryError("instance_invalid")
        if flow_state is not None and flow_state not in _FLOW_STATES:
            raise JdbcQueryError("flow_state_unknown")
        if type(include_absent) is not bool:
            raise JdbcQueryError("include_absent_invalid")
        bounded = max(1, min(DEFAULT_LIMIT if limit is None else limit,
                             self.settings.api_max_page_size))
        return bounded, offset, JdbcFilters(
            needle or None, enabled, protocol, instance_id, flow_state)

    async def list(
        self, *, now: datetime, cadence_seconds: int, width_seconds: int, anchor: int,
        inventory_cadence_seconds: int, truncated_attempt: bool = False,
        limit: int | None = None, offset: int = 0, q: str | None = None,
        enabled: bool | None = None, protocol: int | None = None,
        instance_id: uuid.UUID | None = None, flow_state: str | None = None,
        include_absent: bool = False,
    ) -> JdbcPage:
        """Exactly four data SELECTs; three when the page is empty (nothing to look up)."""
        bounded, start, filters = self.bounds(
            limit=limit, offset=offset, q=q, enabled=enabled, protocol=protocol,
            instance_id=instance_id, flow_state=flow_state, include_absent=include_absent)
        window = queries.target_window(
            now, cadence_seconds=cadence_seconds, width_seconds=width_seconds, anchor=anchor,
            truncated_attempt=truncated_attempt)
        # With the switch off the ABSENT term and column are never added: legacy SQL.
        absent_aware = source_pause_enabled(self.settings)
        omit_absent = absent_aware and not include_absent
        total = int(await self.session.scalar(
            queries.count_query(window, filters, absent_aware=absent_aware,
                                omit_absent=omit_absent)) or 0)
        rows = builtins.list((await self.session.execute(
            queries.page_query(window, filters, limit=bounded, offset=start,
                               absent_aware=absent_aware, omit_absent=omit_absent))).all())
        runs = await self._read(rows, window, now, cadence_seconds, instance_id)
        items = [self._row(r, window, now, cadence_seconds, inventory_cadence_seconds, runs)
                 for r in rows]
        return JdbcPage(items, total, bounded, start,
                        self._snapshot(runs, now, inventory_cadence_seconds))

    async def detail(
        self, log_source_id: uuid.UUID, *, now: datetime, cadence_seconds: int,
        width_seconds: int, anchor: int, inventory_cadence_seconds: int,
        truncated_attempt: bool = False,
    ) -> out.JdbcSourceDetail | None:
        """One source in at most three SELECTs; unknown or non-JDBC-like ids give None."""
        window = queries.target_window(
            now, cadence_seconds=cadence_seconds, width_seconds=width_seconds, anchor=anchor,
            truncated_attempt=truncated_attempt)
        # Absent sources still have a detail row; the default omission is list-only.
        # Select.where composes after limit(), so the page projection serves one id as is.
        stmt = queries.page_query(
            window, JdbcFilters(), limit=1, offset=0,
            absent_aware=source_pause_enabled(self.settings)).where(LogSource.id == log_source_id)
        rows = builtins.list((await self.session.execute(stmt)).all())
        if not rows:
            return None
        runs = await self._read(rows, window, now, cadence_seconds, rows[0].instance_id)
        return self._row(rows[0], window, now, cadence_seconds, inventory_cadence_seconds, runs)

    async def _read(
        self, rows: builtins.list[Any], window: queries.TargetWindow, now: datetime,
        cadence_seconds: int, instance_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        """Statements 3 and 4: last observations of the page ids, then the run state."""
        last: dict[uuid.UUID, flow.LastObservation] = {}
        if rows:
            since = queries.last_observation_since(
                window.end, cadence_seconds=cadence_seconds, width_seconds=window.width_seconds)
            found = await self.session.execute(
                queries.last_observation_query([r.id for r in rows], since=since))
            last = {r.log_source_id: queries.to_last_observation(r) for r in found.all()}
        states = (await self.session.execute(queries.run_state_query(instance_id))).all()
        return {"last": last, "states": {s.instance_id: s for s in states}}

    @staticmethod
    def _snapshot(runs: dict[str, Any], now: datetime, cadence: int) -> JdbcSnapshot:
        fresh = [(flow.inventory_freshness(
            s.inventory_last_complete_at, queries.inventory_attempt_state(s.inventory_pass),
            now, cadence_seconds=cadence), s.inventory_last_complete_at)
            for s in runs["states"].values()]
        states = {f.last_attempt_state for f, _ in fresh}
        state: AttemptState = next((w for w in _WORST if w in states), "NOT_COLLECTED")
        stale = not fresh or any(f.stale for f, _ in fresh)
        stamps = [at for _, at in fresh]
        return JdbcSnapshot(
            bool(fresh) and state == "COMPLETE" and not stale, state, stale,
            None if not stamps or None in stamps else min(stamps), len(fresh))

    @staticmethod
    def _config(row: Any) -> out.JdbcConfigOut:
        if row.protocol_type_id in UNVERIFIED:  # parameters are not read: None, never {}
            return out.JdbcConfigOut(
                support="UNVERIFIED_PROTOCOL", state=ConfigState.UNVERIFIED_PROTOCOL,
                observed_at=None, last_success_at=None, stale=False, safe_fields=None,
                field_states={})
        if not row.config_present:
            return out.JdbcConfigOut(
                support="VERIFIED", state=ConfigState.NOT_COLLECTED, observed_at=None,
                last_success_at=None, stale=True, safe_fields=None, field_states={})
        state = ConfigState(row.cfg_config_state)
        values = {m: getattr(row, f"cfg_{m}") for m in out.JdbcSafeFields.model_fields}
        members = {spec.member for spec in FIELDS}
        degraded: set[str] = set()
        # The same per-field rule as JdbcSafeFields._safe_values, applied one field at a
        # time so a single bad stored value cannot 500 the whole list or detail row.
        for spec in FIELDS:
            value = values[spec.member]
            if value is None:
                continue
            text = str(value).lower() if isinstance(value, bool) else str(value)
            try:
                if spec.parse(text) != value:
                    raise Bad
            except Bad:
                values[spec.member] = None
                degraded.add(spec.member)
        field_states: dict[str, out.JdbcFieldState] = {}
        for member, raw in row.cfg_field_states.items():
            if member not in members:  # JdbcConfigOut._safe_shape forbids unknown keys
                continue
            if member in degraded:
                continue
            try:
                field_states[member] = out.JdbcFieldState(**raw)
            except (TypeError, ValueError):
                values[member] = None
                degraded.add(member)
        if degraded:
            unavailable = out.JdbcFieldState(
                code="UNAVAILABLE", reason=FieldStateReason.UNSUPPORTED_VALUE)
            for member in degraded:
                field_states[member] = unavailable
            logger.warning(
                "jdbc stored config degraded for source %s: %s",
                row.id, ", ".join(sorted(degraded)))
            if state is ConfigState.COMPLETE:
                state = ConfigState.PARTIAL
        return out.JdbcConfigOut(
            support="VERIFIED", state=state, failure_code=row.cfg_failure_code,
            observed_at=row.cfg_observed_at, last_success_at=row.cfg_last_success_at,
            stale=state is not ConfigState.COMPLETE or row.cfg_failure_code is not None,
            safe_fields=out.JdbcSafeFields(**values),
            field_states=field_states)

    def _row(
        self, row: Any, window: queries.TargetWindow, now: datetime, cadence_seconds: int,
        inventory_cadence_seconds: int, runs: dict[str, Any],
    ) -> out.JdbcSourceRow:
        run = runs["states"].get(row.instance_id)
        complete_at = getattr(run, "inventory_last_complete_at", None)
        fresh = flow.inventory_freshness(
            complete_at, queries.inventory_attempt_state(getattr(run, "inventory_pass", None)),
            now, cadence_seconds=inventory_cadence_seconds)
        status, inconsistency = flow.connection_status(row.qradar_status, enabled=row.enabled)
        last = runs["last"].get(row.id)
        observed = last.observed_at if last else row.target_collected_at
        reason: flow.ReasonCode = (
            "disabled_in_qradar" if row.flow_state == "DISABLED_IN_QRADAR"
            else "unmonitored" if row.measurement == "NOT_COLLECTED" and not row.monitoring_enabled
            else _MEASURED_REASON[row.measurement])
        return out.JdbcSourceRow(
            id=row.id, qradar_id=row.qradar_id, name=row.name,
            protocol_type_id=row.protocol_type_id,
            protocol_name="JDBC" if row.protocol_type_id == queries.JDBC_PROTOCOL_ID
            else f"Protocol type {row.protocol_type_id}",
            qradar=out.JdbcQradarOut(
                enabled=row.enabled, status=status, inconsistency=inconsistency,
                inventory_last_complete_at=complete_at,
                inventory_last_attempt_at=getattr(run, "inventory_last_attempt_at", None),
                inventory_state=fresh.last_attempt_state, inventory_stale=fresh.stale,
                reason_code=fresh.reason_code,
                absent_since=getattr(row, "absent_since", None)),
            config=self._config(row),
            volume=out.JdbcVolumeOut(
                measurement=row.measurement, target_start=window.start, target_end=window.end,
                bucket_seconds=window.width_seconds, event_count=row.event_count,
                average_eps=row.average_eps,
                collected_at=row.target_collected_at if row.target_present else None,
                stale=flow.observation_stale(
                    observed, now, cadence_seconds=cadence_seconds,
                    width_seconds=window.width_seconds),
                reason_code=reason,
                last_observation=None if last is None else out.JdbcObservationOut(
                    start=last.start, end=last.end, observed_at=last.observed_at,
                    event_count=last.count, completeness=last.completeness.value,
                    origin=last.origin)),
            flow=out.JdbcFlowOut(state=row.flow_state, reason_code=reason),
            monitoring=out.JdbcMonitoringOut(enabled=row.monitoring_enabled))
