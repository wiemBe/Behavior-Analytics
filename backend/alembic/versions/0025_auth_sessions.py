"""Sessions, directory configuration, and the account columns they need.

ADDITIVE. Revision 0025 chains to 0023 on purpose: F-1's 0024 is not on this
branch. The orchestrator re-chains ``down_revision`` to 0024 at merge time.

``email`` becomes nullable. An LDAP entry with no ``mail`` has no address worth
storing, and a placeholder would be a second identity. PostgreSQL's unique
index already allows many NULLs, so ``ix_app_user_email`` stays. Downgrade
writes ``{id}@unspecified.invalid`` (a reserved name, not a mailbox) and
restores NOT NULL. Those addresses exist only so the old constraint can return.

The seed user ``admin@example.internal`` was created with no password. This
migration disables that row so it cannot sit as a passwordless administrator.
Downgrade marks it active again, because the old code never authenticated it
from the database. The break-glass command creates the real local admin.

``ca_pem`` is a trust anchor, not a secret. No directory password is stored.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0025"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("username", sa.String(length=255), nullable=True))
    op.add_column(
        "app_user",
        sa.Column(
            "auth_source",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'LOCAL'"),
        ),
    )
    op.add_column(
        "app_user",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "app_user",
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "app_user",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "app_user",
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The historical seed admin has no password and no username. Disable it
    # before the server defaults go away, so the row cannot authenticate.
    op.execute(
        "UPDATE app_user SET disabled_at = now(), is_active = false, "
        "must_change_password = true, auth_source = 'LOCAL' "
        "WHERE email = 'admin@example.internal' AND hashed_password IS NULL"
    )
    op.alter_column("app_user", "auth_source", server_default=None)
    op.alter_column("app_user", "must_change_password", server_default=None)
    op.alter_column("app_user", "failed_login_count", server_default=None)
    op.create_index(op.f("ix_app_user_username"), "app_user", ["username"], unique=True)
    op.create_check_constraint(
        op.f("ck_app_user_auth_source_known"),
        "app_user",
        "auth_source IN ('LOCAL', 'LDAP')",
    )
    op.create_check_constraint(
        op.f("ck_app_user_username_lowercase"),
        "app_user",
        "username IS NULL OR username = lower(username)",
    )
    op.create_check_constraint(
        op.f("ck_app_user_ldap_without_password"),
        "app_user",
        "auth_source <> 'LDAP' OR hashed_password IS NULL",
    )
    op.create_check_constraint(
        op.f("ck_app_user_failed_login_count_non_negative"),
        "app_user",
        "failed_login_count >= 0",
    )
    op.alter_column(
        "app_user",
        "email",
        existing_type=sa.String(length=255),
        nullable=True,
        existing_nullable=False,
    )

    op.create_table(
        "ldap_config",
        # autoincrement is off: a sequence default is not a backfill, and the
        # only legal id is the one the INSERT below writes.
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("urls", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("start_tls", sa.Boolean(), nullable=False),
        sa.Column("ca_pem", sa.Text(), nullable=True),
        sa.Column("bind_template", sa.String(length=512), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("user_search_base", sa.String(length=1024), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_ldap_config_singleton")),
        sa.CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 30",
            name=op.f("ck_ldap_config_timeout_range"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_ldap_config_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ldap_config")),
    )
    op.execute(
        "INSERT INTO ldap_config "
        "(id, enabled, urls, start_tls, bind_template, timeout_seconds, version, updated_at) "
        "VALUES (1, false, '[]'::jsonb, false, '{username}', 5, 1, now())"
    )

    op.create_table(
        "auth_session",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_auth_session_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_session")),
    )
    op.create_index(
        op.f("ix_auth_session_token_hash"), "auth_session", ["token_hash"], unique=True
    )
    op.create_index(op.f("ix_auth_session_user_id"), "auth_session", ["user_id"])
    op.create_index(op.f("ix_auth_session_expires_at"), "auth_session", ["expires_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_session_expires_at"), table_name="auth_session")
    op.drop_index(op.f("ix_auth_session_user_id"), table_name="auth_session")
    op.drop_index(op.f("ix_auth_session_token_hash"), table_name="auth_session")
    op.drop_table("auth_session")
    op.drop_table("ldap_config")

    op.execute(
        "UPDATE app_user SET is_active = true "
        "WHERE email = 'admin@example.internal' AND hashed_password IS NULL"
    )
    # A reserved, non-routable name so NOT NULL can return. Not a mailbox.
    op.execute(
        "UPDATE app_user SET email = id::text || '@unspecified.invalid' WHERE email IS NULL"
    )
    op.alter_column(
        "app_user",
        "email",
        existing_type=sa.String(length=255),
        nullable=False,
        existing_nullable=True,
    )
    op.drop_constraint(
        op.f("ck_app_user_failed_login_count_non_negative"), "app_user", type_="check"
    )
    op.drop_constraint(op.f("ck_app_user_ldap_without_password"), "app_user", type_="check")
    op.drop_constraint(op.f("ck_app_user_username_lowercase"), "app_user", type_="check")
    op.drop_constraint(op.f("ck_app_user_auth_source_known"), "app_user", type_="check")
    op.drop_index(op.f("ix_app_user_username"), table_name="app_user")
    op.drop_column("app_user", "disabled_at")
    op.drop_column("app_user", "locked_until")
    op.drop_column("app_user", "failed_login_count")
    op.drop_column("app_user", "must_change_password")
    op.drop_column("app_user", "auth_source")
    op.drop_column("app_user", "username")
