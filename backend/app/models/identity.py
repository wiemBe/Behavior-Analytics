"""User, Role, sessions, directory configuration, and the append-only AuditLog.

Sign-in is a local bcrypt password or an LDAP bind as the user. The directory
password is never stored. `hashed_password` stays NULL for LDAP accounts and
for any legacy row that was created before passwords existed. `email` is
optional: an LDAP entry with no `mail` has nothing honest to put there.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AuthSource

user_role = Table(
    "user_role_link",
    Base.metadata,
    Column("user_id", PgUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"),
           primary_key=True),
    Column("role_id", PgUUID(as_uuid=True), ForeignKey("role.id", ondelete="CASCADE"),
           primary_key=True),
)


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "role"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Coarse capability strings, e.g. ["search:execute", "alert:ack", "admin:*"].
    # RBAC is checked against this list; see app.security.rbac.
    permissions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    users: Mapped[list[User]] = relationship(
        secondary=user_role, back_populates="roles"
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint("auth_source IN ('LOCAL', 'LDAP')", name="auth_source_known"),
        CheckConstraint(
            "username IS NULL OR username = lower(username)",
            name="username_lowercase",
        ),
        CheckConstraint(
            "auth_source <> 'LDAP' OR hashed_password IS NULL",
            name="ldap_without_password",
        ),
        CheckConstraint(
            "failed_login_count >= 0",
            name="failed_login_count_non_negative",
        ),
    )

    # NULL when the directory entry has no mail. The unique index allows many
    # NULLs; a synthetic address would be a second, false identity.
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    # Stored lower-case. NULL only on legacy rows that predate sign-in.
    username: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    auth_source: Mapped[str] = mapped_column(
        String(8), nullable=False, default=AuthSource.LOCAL.value
    )

    # LOCAL only. NULL for LDAP and for a legacy row that has no password.
    hashed_password: Mapped[str | None] = mapped_column(String(255))
    # OIDC subject claim; NULL for local and LDAP users. OIDC itself is refused.
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list[Role]] = relationship(
        secondary=user_role, back_populates="users", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<User {self.username or self.email}>"


class AuthSession(UUIDPrimaryKeyMixin, Base):
    """A server-side session. The cookie carries the random token; this row
    stores only its SHA-256 hex digest."""

    __tablename__ = "auth_session"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class LdapConfig(Base):
    """The one directory connection. Authentication binds as the user, so this
    row holds no password. `ca_pem` is a trust anchor, which is public."""

    __tablename__ = "ldap_config"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 30",
            name="timeout_range",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    # No sequence. The only legal id is 1, and a column default would survive
    # as a backfill leftover. The application sets it.
    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=False, default=1
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    urls: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    start_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ca_pem: Mapped[str | None] = mapped_column(Text)
    bind_template: Mapped[str] = mapped_column(String(512), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    user_search_base: Mapped[str | None] = mapped_column(String(1024))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Append-only record of administrative and search actions.

    No updated_at, no ORM-level delete path is ever exposed. Records who did
    what to which object, with a redacted parameter payload — never raw secrets
    or raw event data.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_actor_time", "actor", "created_at"),
        Index("ix_audit_log_action", "action", "created_at"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    actor: Mapped[str | None] = mapped_column(String(255))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    # e.g. "search.execute", "search.update", "alert.acknowledge",
    # "instance.create", "auth.login"
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str | None] = mapped_column(String(64))
    object_id: Mapped[str | None] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(16), default="SUCCESS", nullable=False)

    source_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    # Redacted before persistence by app.security.redaction.
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
