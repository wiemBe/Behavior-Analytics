"""Deterministic admission of new WAF endpoints.

The normalizer bounds the *structure* of an endpoint, not the *number* of them. A
scanner requesting `/aaaa`, `/aaab`, ... produces a new normalized endpoint per
path, and every endpoint that becomes an entity is zero-filled each bucket until
it retires. Without admission control, request paths -- which anyone can choose
-- decide how fast the database grows.

Admission applies only to endpoints the source does not already actively track.
Tracked endpoints are always collected, whatever the quota: an attacker who
floods a source with new paths must not be able to stop collection of the
endpoints that were already being watched.

For the unseen endpoints in one bucket:

  1. an endpoint below `min_requests` in the bucket is not admitted;
  2. the rest are ordered by request volume, descending, with a total tie-break
     on (virtual host, endpoint, method), so the same bucket always admits the
     same endpoints regardless of the order QRadar returned them in;
  3. the first `min(max_new_per_bucket, max_active - active)` are admitted.

Everything not admitted is counted -- endpoints and requests -- and never stored
by name. Pure functions, no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class AdmissionCandidate(Protocol):
    @property
    def virtual_host(self) -> str | None: ...

    @property
    def endpoint(self) -> str: ...

    @property
    def method(self) -> str: ...

    @property
    def request_count(self) -> int: ...


@dataclass(frozen=True)
class AdmissionPolicy:
    max_new_per_bucket: int
    max_active: int
    min_requests: int


@dataclass(frozen=True)
class AdmissionDecision[C: AdmissionCandidate]:
    admitted: list[C]
    below_threshold: list[C]
    over_quota: list[C]
    #: New endpoints this bucket was allowed to admit.
    quota: int

    @property
    def truncated(self) -> bool:
        """Eligible endpoints were refused for want of quota."""
        return bool(self.over_quota)

    @property
    def untracked(self) -> list[C]:
        return [*self.below_threshold, *self.over_quota]

    @property
    def untracked_requests(self) -> int:
        return sum(c.request_count for c in self.untracked)


def admission_order_key(candidate: AdmissionCandidate) -> tuple[int, str, str, str]:
    """Most requests first, then a total order on the identifying fields.

    A virtual host is either NULL or non-empty (the normalizer never returns an
    empty string), so mapping NULL to "" cannot collide with a real host.
    """
    return (
        -candidate.request_count,
        candidate.virtual_host or "",
        candidate.endpoint,
        candidate.method,
    )


def decide_admission[C: AdmissionCandidate](
    unseen: Sequence[C], *, active_count: int, policy: AdmissionPolicy
) -> AdmissionDecision[C]:
    """Which unseen endpoints become tracked entities in this bucket."""
    below = [c for c in unseen if c.request_count < policy.min_requests]
    eligible = sorted(
        (c for c in unseen if c.request_count >= policy.min_requests),
        key=admission_order_key,
    )
    quota = max(0, min(policy.max_new_per_bucket, policy.max_active - active_count))
    return AdmissionDecision(
        admitted=eligible[:quota],
        below_threshold=sorted(below, key=admission_order_key),
        over_quota=eligible[quota:],
        quota=quota,
    )
