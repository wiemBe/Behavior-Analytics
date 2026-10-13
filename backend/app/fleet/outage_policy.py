"""Pure resolution of the Phase C.2 outage alert policy.

The engine and the APIs both need one answer for "does this source's silence
alert, and after how many empty buckets?". That answer is a pure function of
the policy rows and the source's facts, so it lives here and nowhere else.

Precedence, highest first:

  1. a ``SOURCE`` row for this log source;
  2. a ``CLASS`` row for the source's effective class;
  3. the configured default.

Within one scope an instance-specific row wins over a global (``instance_id``
NULL) row, because a row authored for one console is more specific than one
authored for every console. ``basis`` records which of the three applied.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from app.models.enums import OutagePolicyMode, OutagePolicyScope


class OutagePolicyRow(Protocol):
    """The shape :func:`resolve` reads. Duck-typed so both the ORM row and a
    test double qualify without importing the model."""

    instance_id: uuid.UUID | None
    scope_kind: OutagePolicyScope
    scope_value: str
    mode: OutagePolicyMode
    silence_grace_buckets: int | None


@dataclass(frozen=True)
class ResolvedOutage:
    mode: OutagePolicyMode
    #: None means "no override": the caller keeps ``anomaly_silence_grace_buckets``.
    silence_grace_buckets: int | None
    #: SOURCE, CLASS or DEFAULT -- which rule produced this verdict.
    basis: str


def _pick(
    rows: list[OutagePolicyRow],
    scope: OutagePolicyScope,
    value: str,
    instance_id: uuid.UUID | None,
) -> OutagePolicyRow | None:
    """The winning row for one scope, or None.

    An instance-specific row for the caller's instance beats a global one; a
    row for a different instance never applies. With no instance in hand (the
    fleet-wide summary), only global rows can apply.
    """
    candidates = [
        row for row in rows if row.scope_kind == scope.value and row.scope_value == value
    ]
    if not candidates:
        return None
    if instance_id is not None:
        for row in candidates:
            if row.instance_id == instance_id:
                return row
    return next((row for row in candidates if row.instance_id is None), None)


def resolve(
    policy_rows: Iterable[OutagePolicyRow],
    *,
    log_source_id: uuid.UUID | None,
    source_class: str | None,
    instance_id: uuid.UUID | None,
    default: OutagePolicyMode,
) -> ResolvedOutage:
    """Resolve the outage mode and silence grace for one source.

    ``log_source_id`` is None for a class-only resolution (the fleet summary
    asks "what does this class do, ignoring source overrides?").
    """
    rows = list(policy_rows)
    if log_source_id is not None:
        row = _pick(rows, OutagePolicyScope.SOURCE, str(log_source_id), instance_id)
        if row is not None:
            return ResolvedOutage(
                OutagePolicyMode(row.mode), row.silence_grace_buckets, "SOURCE"
            )
    if source_class is not None:
        row = _pick(rows, OutagePolicyScope.CLASS, str(source_class), instance_id)
        if row is not None:
            return ResolvedOutage(
                OutagePolicyMode(row.mode), row.silence_grace_buckets, "CLASS"
            )
    return ResolvedOutage(default, None, "DEFAULT")
