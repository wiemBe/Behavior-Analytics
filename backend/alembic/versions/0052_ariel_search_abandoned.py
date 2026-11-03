"""Terminal outcome ``ABANDONED`` for Ariel load rows whose worker died.

ADDITIVE. A row is written ``IN_FLIGHT`` at submit and closed by its worker's
terminal write. When the worker dies first, the reaper (beat task
``reap_abandoned_ariel_searches``, every ``ARIEL_SEARCH_ABANDON_SCAN_SECONDS``)
now closes the row as ``ABANDONED``: the search did load QRadar, so the summary
must count it instead of silently dropping it. ``finished_at`` is set, every
measured column stays NULL (unknown, never 0), and a terminal write that
arrives later still overwrites the guess.

Downgrade turns every ``ABANDONED`` row back into ``IN_FLIGHT`` — the state it
really had: no end was recorded — and restores the old outcome check.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_ariel_search_run_outcome_known"
# The order matches the model's ``OUTCOMES`` so ``alembic check`` sees no drift.
_OUTCOMES_OLD = (
    "'IN_FLIGHT', 'COMPLETED', 'ERROR', 'CANCELED', 'TIMEOUT', 'REFUSED', "
    "'PROVIDER_UNAVAILABLE'"
)
_OUTCOMES_NEW = (
    "'IN_FLIGHT', 'COMPLETED', 'ERROR', 'CANCELED', 'TIMEOUT', 'REFUSED', "
    "'PROVIDER_UNAVAILABLE', 'ABANDONED'"
)


def upgrade() -> None:
    # ``op.f``: the name is final, the naming convention must not prefix it again.
    op.drop_constraint(op.f(_CONSTRAINT), "ariel_search_run", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT),
        "ariel_search_run",
        f"outcome IN ({_OUTCOMES_NEW})",
    )


def downgrade() -> None:
    # Back to the state the row really had: the worker never recorded an end.
    op.execute(
        "UPDATE ariel_search_run SET outcome = 'IN_FLIGHT', finished_at = NULL "
        "WHERE outcome = 'ABANDONED'"
    )
    op.drop_constraint(op.f(_CONSTRAINT), "ariel_search_run", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT),
        "ariel_search_run",
        f"outcome IN ({_OUTCOMES_OLD})",
    )
