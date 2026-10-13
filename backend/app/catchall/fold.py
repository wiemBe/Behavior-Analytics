"""Pure fold from one catch-all source's keyed rows to a bucket fact (design C.8).

The sender text (`logsourceidentifier`) is chosen by whoever sends: a scanner or a
misconfigured fleet can mint a new one per event. So the fold bounds what that text
can create (AGENTS.md rule 3):

* a value that is not printable, empty once stripped, or longer than
  `IDENTIFIER_MAX` is not a sender; its events are counted as untracked;
* a sender the source already tracks is always kept, whatever the quota, so a
  flood of new names cannot stop the watched ones being recorded;
* an unseen sender goes through the WAF admission rule (`app.waf.admission`): at
  least `min_events` in the bucket, then the busiest first with the identifier as
  a total tie-break, up to the per-bucket quota and the active ceiling;
* everything refused is counted, never stored by name.

What leaves the fold is bounded: counts, and at most `top_n` `(identifier, events)`
pairs of tracked senders. A bucket without a measurement is not folded at all.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.models.enums import CatchallMeasurement
from app.providers.dto import CatchallSenderRow
from app.waf.admission import AdmissionPolicy, decide_admission

IDENTIFIER_MAX = 255


class CatchallFoldError(ValueError):
    """A fold was handed something the collection contract rules out: a bug, not data."""


def sanitize_identifier(raw: str | None) -> str | None:
    """The stored form of a sender, or None when the value is not a sender."""
    if raw is None:
        return None
    value = raw.strip()
    if not value or len(value) > IDENTIFIER_MAX or not value.isprintable():
        return None
    return value


@dataclass
class SenderLedger:
    """The senders one catch-all source tracks as active. Mutated as buckets fold in order."""

    active: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class SenderCandidate:
    """An unseen sender, shaped for the WAF admission rule.

    The rule orders by `(-request_count, virtual_host, endpoint, method)`; here the
    identifier is the endpoint, so the order is volume then identifier.
    """

    endpoint: str
    request_count: int
    virtual_host: str | None = None
    method: str = ""


@dataclass(frozen=True)
class CatchallBucketFact:
    """What one catch-all source's bucket established. Counts and a bounded top list."""

    measurement: CatchallMeasurement
    events: int
    records: int
    #: Distinct sender values seen (an invalid value counts once per row).
    distinct_senders: int
    #: True when the bucket was truncated: more senders may exist than were seen.
    distinct_senders_lower_bound: bool
    #: At most `top_n` `(identifier, events)` of tracked senders, busiest first.
    top_senders: tuple[tuple[str, int], ...]
    admitted: int
    below_admission_threshold: int
    rejected_over_quota: int
    untracked_senders: int
    untracked_events: int
    #: Identifiers admitted in this bucket (not tracked before it), sorted.
    admitted_identifiers: tuple[str, ...]
    #: Every tracked sender of the bucket with its events, for the sender table.
    tracked_events: dict[str, int]


def fold_source_bucket(
    rows: Iterable[CatchallSenderRow],
    ledger: SenderLedger,
    *,
    measurement: CatchallMeasurement,
    policy: AdmissionPolicy,
    top_n: int,
) -> CatchallBucketFact:
    """Fold one source's rows of one measured bucket, admitting its new senders.

    `rows` is empty for a measured zero (a COMPLETE bucket in which the source
    sent nothing). The ledger gains the senders admitted here.
    """
    if top_n < 1:
        raise CatchallFoldError("top_n must be positive")
    total_events = 0
    total_records = 0
    merged: dict[str, int] = defaultdict(int)
    invalid_rows = 0
    invalid_events = 0
    for row in rows:
        total_events += row.events
        total_records += row.records
        identifier = sanitize_identifier(row.sender)
        if identifier is None:
            invalid_rows += 1
            invalid_events += row.events
        else:
            merged[identifier] += row.events

    tracked = {name: events for name, events in merged.items() if name in ledger.active}
    unseen = [
        SenderCandidate(name, events) for name, events in merged.items() if name not in tracked
    ]
    decision = decide_admission(unseen, active_count=len(ledger.active), policy=policy)
    admitted = sorted(c.endpoint for c in decision.admitted)
    for candidate in decision.admitted:
        tracked[candidate.endpoint] = candidate.request_count
    ledger.active.update(admitted)

    refused = decision.untracked
    top = sorted(tracked.items(), key=lambda item: (-item[1], item[0]))[:top_n]
    return CatchallBucketFact(
        measurement=measurement,
        events=total_events,
        records=total_records,
        distinct_senders=len(merged) + invalid_rows,
        distinct_senders_lower_bound=measurement is CatchallMeasurement.LOWER_BOUND,
        top_senders=tuple(top),
        admitted=len(admitted),
        below_admission_threshold=len(decision.below_threshold),
        rejected_over_quota=len(decision.over_quota),
        untracked_senders=len(refused) + invalid_rows,
        untracked_events=sum(c.request_count for c in refused) + invalid_events,
        admitted_identifiers=tuple(admitted),
        tracked_events=tracked,
    )
