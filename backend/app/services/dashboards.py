"""Saved dashboards (UR.5.5): visibility, ownership, optimistic concurrency, limits, audit.

The layout is stored and returned as validated structure. Nothing here reads `config`, and
nothing here logs or audits a layout: an audit `detail` is `{name, revision, widget_count}`.

Rulings on top of UR.5.5 (docs/tasks/B-4.md):
  * a caller sees their own dashboards and every SHARED one; another owner's PRIVATE dashboard
    is 404 for every route, never 403, so its existence is not revealed (admins included);
  * PUT, PATCH and DELETE need ownership or `admin:*`; any change of `visibility` needs
    `admin:dashboards`;
  * PUT replaces with one `UPDATE ... WHERE id AND revision = expected`, never read-then-write.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dashboard import Dashboard, DashboardVisibility, UserDashboardPref
from app.repositories.audit import record_audit
from app.schemas.dashboard import (
    MAX_DASHBOARDS_PER_OWNER,
    MAX_NAME_LENGTH,
    DashboardCreate,
    DashboardListItem,
    DashboardOut,
    DashboardPatch,
    DashboardReplace,
    PagedDashboards,
    upgrade_layout,
)
from app.security.rbac import PERM_ADMIN, PERM_DASHBOARD_SHARE, Principal

_NAME_CONSTRAINT = "uq_dashboard_owner_subject_name"


class DashboardError(Exception):
    """A refused dashboard operation: the HTTP status and a body the route returns as is."""

    def __init__(self, status_code: int, detail: str | dict[str, Any]) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


def _not_found() -> DashboardError:
    return DashboardError(404, "dashboard not found")


def _name_taken(name: str) -> DashboardError:
    return DashboardError(409, f"you already have a dashboard named '{name}'")


def widget_count(layout: dict[str, Any]) -> int:
    return len(layout.get("widgets", []))


def _visible_to(subject: str):  # type: ignore[no-untyped-def]
    return or_(
        Dashboard.owner_subject == subject,
        Dashboard.visibility == DashboardVisibility.SHARED.value,
    )


def _is_name_conflict(exc: IntegrityError) -> bool:
    return _NAME_CONSTRAINT in str(exc.orig) or _NAME_CONSTRAINT in str(exc)


async def effective_default_id(session: AsyncSession, subject: str) -> uuid.UUID | None:
    """The caller's default, or None when unset or no longer readable by them."""
    return await session.scalar(
        select(Dashboard.id)
        .join(UserDashboardPref, UserDashboardPref.default_dashboard_id == Dashboard.id)
        .where(UserDashboardPref.owner_subject == subject, _visible_to(subject))
    )


def _out(row: Dashboard, default_id: uuid.UUID | None) -> DashboardOut:
    return DashboardOut(
        id=row.id,
        name=row.name,
        description=row.description,
        owner_subject=row.owner_subject,
        visibility=DashboardVisibility(row.visibility),
        template_key=row.template_key,
        template_version=row.template_version,
        schema_version=row.schema_version,
        layout=upgrade_layout(row.layout),
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
        is_default=row.id == default_id,
    )


async def _visible_row(session: AsyncSession, dashboard_id: uuid.UUID, subject: str) -> Dashboard:
    row = await session.scalar(
        select(Dashboard)
        .where(Dashboard.id == dashboard_id, _visible_to(subject))
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise _not_found()
    return row


def _require_owner_or_admin(row: Dashboard, principal: Principal) -> None:
    if row.owner_subject != principal.subject and not principal.has(PERM_ADMIN):
        raise DashboardError(403, "only the owner or an administrator can change this dashboard")


async def _audit(
    session: AsyncSession, principal: Principal, action: str, row: Dashboard
) -> None:
    await record_audit(
        session,
        action=action,
        actor=principal.subject,
        object_type="dashboard",
        object_id=str(row.id),
        detail={
            "name": row.name,
            "revision": row.revision,
            "widget_count": widget_count(row.layout),
        },
    )


async def _guard_owner_limit(session: AsyncSession, subject: str) -> set[str]:
    """Serialize one owner's inserts, then refuse the 21st. Returns the owner's names."""
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(subject))))
    owned = await session.scalars(select(Dashboard.name).where(Dashboard.owner_subject == subject))
    names = set(owned)
    if len(names) >= MAX_DASHBOARDS_PER_OWNER:
        raise DashboardError(
            409, f"the limit is {MAX_DASHBOARDS_PER_OWNER} dashboards per owner"
        )
    return names


async def list_dashboards(
    session: AsyncSession, principal: Principal, *, limit: int, offset: int
) -> PagedDashboards:
    subject = principal.subject
    default_id = await effective_default_id(session, subject)
    where = _visible_to(subject)
    total = await session.scalar(select(func.count()).select_from(Dashboard).where(where))
    count = func.jsonb_array_length(Dashboard.layout["widgets"])
    stmt: Select[Any] = (
        select(
            Dashboard.id,
            Dashboard.name,
            Dashboard.owner_subject,
            Dashboard.visibility,
            Dashboard.template_key,
            Dashboard.updated_at,
            count,
        )
        .where(where)
        .order_by(Dashboard.updated_at.desc(), Dashboard.id)
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).all()
    return PagedDashboards(
        items=[
            DashboardListItem(
                id=row[0],
                name=row[1],
                owner_subject=row[2],
                visibility=DashboardVisibility(row[3]),
                template_key=row[4],
                updated_at=row[5],
                widget_count=row[6] or 0,
                is_default=row[0] == default_id,
            )
            for row in rows
        ],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def get_dashboard(
    session: AsyncSession, principal: Principal, dashboard_id: uuid.UUID
) -> DashboardOut:
    row = await _visible_row(session, dashboard_id, principal.subject)
    return _out(row, await effective_default_id(session, principal.subject))


async def create_dashboard(
    session: AsyncSession, principal: Principal, body: DashboardCreate
) -> DashboardOut:
    names = await _guard_owner_limit(session, principal.subject)
    if body.name in names:
        raise _name_taken(body.name)
    row = Dashboard(
        owner_subject=principal.subject,
        name=body.name,
        description=body.description,
        visibility=DashboardVisibility.PRIVATE.value,
        template_key=body.template_key,
        template_version=body.template_version,
        schema_version=body.layout.v,
        layout=body.layout.model_dump(mode="json"),
        revision=1,
        updated_by=principal.subject,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_name_conflict(exc):
            raise _name_taken(body.name) from exc
        raise
    await session.refresh(row)
    await _audit(session, principal, "dashboard.create", row)
    return _out(row, await effective_default_id(session, principal.subject))


async def replace_dashboard(
    session: AsyncSession,
    principal: Principal,
    dashboard_id: uuid.UUID,
    body: DashboardReplace,
) -> DashboardOut:
    row = await _visible_row(session, dashboard_id, principal.subject)
    _require_owner_or_admin(row, principal)
    previous_name = row.name
    # One atomic statement conditioned on the revision the client read. A second writer finds
    # revision != expected, updates nothing, and gets the current revision back.
    stmt = (
        update(Dashboard)
        .where(Dashboard.id == dashboard_id, Dashboard.revision == body.expected_revision)
        .values(
            name=body.name,
            description=body.description,
            schema_version=body.layout.v,
            layout=body.layout.model_dump(mode="json"),
            revision=Dashboard.revision + 1,
            updated_by=principal.subject,
            updated_at=func.now(),
        )
        .returning(Dashboard)
        .execution_options(populate_existing=True, synchronize_session=False)
    )
    try:
        updated = (await session.execute(stmt)).scalar_one_or_none()
    except IntegrityError as exc:
        if _is_name_conflict(exc):
            raise _name_taken(body.name) from exc
        raise
    if updated is None:
        current = await session.scalar(
            select(Dashboard.revision).where(Dashboard.id == dashboard_id)
        )
        if current is None:
            raise _not_found()
        raise DashboardError(
            409,
            {
                "message": "this dashboard changed since you opened it",
                "current_revision": current,
            },
        )
    # Every PUT replaces the layout, so every PUT is an update; a changed name is also a rename.
    await _audit(session, principal, "dashboard.update", updated)
    if updated.name != previous_name:
        await _audit(session, principal, "dashboard.rename", updated)
    return _out(updated, await effective_default_id(session, principal.subject))


async def patch_dashboard(
    session: AsyncSession,
    principal: Principal,
    dashboard_id: uuid.UUID,
    body: DashboardPatch,
) -> DashboardOut:
    row = await _visible_row(session, dashboard_id, principal.subject)
    _require_owner_or_admin(row, principal)
    values: dict[str, Any] = {}
    actions: list[str] = []
    if body.name is not None and body.name != row.name:
        values["name"] = body.name
        actions.append("dashboard.rename")
    if "description" in body.model_fields_set and body.description != row.description:
        values["description"] = body.description
    if body.visibility is not None and body.visibility.value != row.visibility:
        if not principal.has(PERM_DASHBOARD_SHARE):
            raise DashboardError(
                403, f"changing visibility needs the {PERM_DASHBOARD_SHARE} permission"
            )
        values["visibility"] = body.visibility.value
        actions.append("dashboard.share")
    stmt = (
        update(Dashboard)
        .where(Dashboard.id == dashboard_id)
        .values(
            **values,
            revision=Dashboard.revision + 1,
            updated_by=principal.subject,
            updated_at=func.now(),
        )
        .returning(Dashboard)
        .execution_options(populate_existing=True, synchronize_session=False)
    )
    try:
        updated = (await session.execute(stmt)).scalar_one_or_none()
    except IntegrityError as exc:
        if body.name is not None and _is_name_conflict(exc):
            raise _name_taken(body.name) from exc
        raise
    if updated is None:
        raise _not_found()
    for action in actions or ["dashboard.update"]:
        await _audit(session, principal, action, updated)
    return _out(updated, await effective_default_id(session, principal.subject))


async def delete_dashboard(
    session: AsyncSession, principal: Principal, dashboard_id: uuid.UUID
) -> None:
    row = await _visible_row(session, dashboard_id, principal.subject)
    _require_owner_or_admin(row, principal)
    # The foreign key's ON DELETE SET NULL clears every default pointing at it.
    await session.execute(delete(Dashboard).where(Dashboard.id == dashboard_id))
    await _audit(session, principal, "dashboard.delete", row)


def copy_name(source_name: str, taken: set[str]) -> str:
    """'Copy of {name}', then '... (2)', '(3)'..., cut so the whole name fits 80 characters."""
    base = f"Copy of {source_name}"
    number = 1
    while True:
        suffix = "" if number == 1 else f" ({number})"
        candidate = base[: MAX_NAME_LENGTH - len(suffix)].rstrip() + suffix
        if candidate not in taken:
            return candidate
        number += 1


async def duplicate_dashboard(
    session: AsyncSession, principal: Principal, dashboard_id: uuid.UUID
) -> DashboardOut:
    source = await _visible_row(session, dashboard_id, principal.subject)
    names = await _guard_owner_limit(session, principal.subject)
    name = copy_name(source.name, names)
    row = Dashboard(
        owner_subject=principal.subject,
        name=name,
        description=source.description,
        visibility=DashboardVisibility.PRIVATE.value,
        template_key=source.template_key,
        template_version=source.template_version,
        schema_version=source.schema_version,
        layout=upgrade_layout(source.layout),
        revision=1,
        updated_by=principal.subject,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        if _is_name_conflict(exc):
            raise _name_taken(name) from exc
        raise
    await session.refresh(row)
    await _audit(session, principal, "dashboard.duplicate", row)
    return _out(row, await effective_default_id(session, principal.subject))


async def set_default(
    session: AsyncSession, principal: Principal, dashboard_id: uuid.UUID | None
) -> uuid.UUID | None:
    target: Dashboard | None = None
    if dashboard_id is not None:
        target = await _visible_row(session, dashboard_id, principal.subject)
    insert = pg_insert(UserDashboardPref).values(
        owner_subject=principal.subject, default_dashboard_id=dashboard_id
    )
    await session.execute(
        insert.on_conflict_do_update(
            index_elements=[UserDashboardPref.owner_subject],
            set_={"default_dashboard_id": dashboard_id, "updated_at": func.now()},
        )
    )
    if target is not None:
        await _audit(session, principal, "dashboard.set_default", target)
    else:
        await record_audit(
            session,
            action="dashboard.set_default",
            actor=principal.subject,
            object_type="dashboard",
            detail={"cleared": True},
        )
    return dashboard_id
