"""Decide whether an identity profile may collect. No I/O and no message catalog.

The probe is counts only. A rejected field wins over an inconclusive probe.
A zero, a cut, or an error is never a pass. The report stores roles and
counts, never a property name and never the query.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.identity.dto import IdentityProbeResult
from app.models.enums import IdentityProfileValidationStatus


@dataclass(frozen=True)
class IdentityValidation:
    """The stored outcome of one probe."""

    status: IdentityProfileValidationStatus
    report: dict[str, object]


def evaluate_probe(
    probe: IdentityProbeResult,
    enabled_event_ids: tuple[int, ...],
    *,
    min_ratio: float,
) -> IdentityValidation:
    """Map a probe to VALID, INVALID or INCONCLUSIVE.

    ``enabled_event_ids`` is the profile's catalog selection. Overlap is
    counted from the probe's catalog ids; the ids themselves are catalog
    integers, not operator text.
    """
    enabled = set(enabled_event_ids)
    present = tuple(event_id for event_id in probe.catalog_event_ids if event_id in enabled)
    rejected = probe.event_id.rejected or probe.username.rejected
    total = probe.total_events
    if rejected:
        status = IdentityProfileValidationStatus.INVALID
    elif (
        probe.error
        or probe.truncated
        or total is None
        or probe.event_id.error
        or probe.username.error
        or total == 0
    ):
        status = IdentityProfileValidationStatus.INCONCLUSIVE
    elif (
        _below_ratio(probe.event_id.populated_events, total, min_ratio)
        or _below_ratio(probe.username.populated_events, total, min_ratio)
        or not present
    ):
        status = IdentityProfileValidationStatus.INVALID
    else:
        status = IdentityProfileValidationStatus.VALID
    report: dict[str, object] = {
        "status": status.value,
        "total_events": total,
        "event_id_populated": probe.event_id.populated_events,
        "username_populated": probe.username.populated_events,
        "event_id_rejected": probe.event_id.rejected,
        "username_rejected": probe.username.rejected,
        "event_id_error": probe.event_id.error,
        "username_error": probe.username.error,
        "catalog_overlap": len(present),
        "min_ratio": min_ratio,
        "searches": probe.searches,
        "truncated": probe.truncated,
        "error": probe.error,
    }
    return IdentityValidation(status=status, report=report)


def _below_ratio(populated: int | None, total: int, min_ratio: float) -> bool:
    if populated is None or total <= 0:
        return True
    return populated / total < min_ratio
