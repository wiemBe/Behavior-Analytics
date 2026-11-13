"""SOC-10de: allow IDENTITY_TOP on both entity-mention source checks.

ADDITIVE. Reads the live CHECK and appends ``IDENTITY_TOP`` when it is missing,
so a later head that already widened the same check is not replaced by a frozen
list. Downgrade deletes the derived IDENTITY_TOP mention and backfill rows
(they are an index, not analyst data) and then removes only that token.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TOKEN = "IDENTITY_TOP"
_CHECKS = (
    ("entity_mention", "ck_entity_mention_source_known"),
    ("entity_mention_backfill", "ck_entity_mention_backfill_source_known"),
)
_SOURCE_TOKEN = re.compile(r"^[A-Z][A-Z0-9_]{0,23}$")
_QUOTED = re.compile(r"'((?:[^']|'')*)'")


def _definition(table: str, name: str) -> str:
    row = op.get_bind().execute(
        sa.text(
            """
            SELECT pg_get_constraintdef(pg_constraint.oid)
            FROM pg_constraint
            JOIN pg_class ON pg_class.oid = pg_constraint.conrelid
            WHERE pg_class.relname = :table
              AND pg_constraint.conname = :name
            """
        ),
        {"table": table, "name": name},
    ).scalar_one()
    return str(row)


def _tokens(definition: str) -> list[str]:
    found: list[str] = []
    for raw in _QUOTED.findall(definition):
        token = raw.replace("''", "'")
        if _SOURCE_TOKEN.fullmatch(token) and token not in found:
            found.append(token)
    if not found:
        raise RuntimeError(f"entity mention source check has no tokens: {definition}")
    return found


def _apply(table: str, name: str, tokens: list[str]) -> None:
    quoted = ", ".join(f"'{token}'" for token in tokens)
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, f"source IN ({quoted})")


def _retoken(table: str, name: str, *, add: str | None, remove: str | None) -> None:
    tokens = _tokens(_definition(table, name))
    if add is not None and add not in tokens:
        tokens.append(add)
    if remove is not None:
        tokens = [token for token in tokens if token != remove]
    _apply(table, name, tokens)


def upgrade() -> None:
    for table, name in _CHECKS:
        _retoken(table, name, add=_TOKEN, remove=None)


def downgrade() -> None:
    # Mentions are a derived index. Dropping only IDENTITY_TOP leaves every other
    # source, including a token a later revision added, in place.
    op.execute(sa.text("DELETE FROM entity_mention WHERE source = 'IDENTITY_TOP'"))
    op.execute(sa.text("DELETE FROM entity_mention_backfill WHERE source = 'IDENTITY_TOP'"))
    for table, name in _CHECKS:
        _retoken(table, name, add=None, remove=_TOKEN)
