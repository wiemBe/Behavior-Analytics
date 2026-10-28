"""Scope resolution for property watches (design §G.4). Pure: no I/O, no clock of its own.

A watch names one log source, one log source type or one log source group. Resolution turns
that into what the searches speak -- QRadar ids -- from the synced inventory, which the
caller reads **once** for every watch of the instance (AGENTS.md rule 9):

  * a source resolves to its own `qradar_id`;
  * a type resolves to the type id, which the appliance matches (`devicetype = <id>`), and
    to a member count of the synced sources of that type, with no fingerprint: membership of
    a type is decided on the appliance, so a new source of the type is part of what the
    scope means;
  * a group resolves to the `qradar_id` of every source whose `group_ids` holds the group or
    any descendant group. **The walk is cycle-safe**: the lab's own two groups name each
    other as parent, so it keeps a visited set and meets each group once.

A scope that cannot be trusted resolves to a reason, never to a guess: a vanished or empty
group is `SCOPE_EMPTY`, a group row older than `pw_scope_stale_after_hours` is `SCOPE_STALE`
(a failed group listing is swallowed by the sync, so its rows keep their old `synced_at`),
and a group above `pw_max_chunks_per_watch` chunks is `SCOPE_TOO_LARGE`. Each of them writes
no bucket (AGENTS.md rule 8). Their `groups_walked`, `member_count` and `members` still carry
what the last sync held, so the API's `/scope` read can show it marked with the status; only a
`resolved` scope ever searches or persists.

`scope_changed_flags` decides which buckets of a tick carry `scope_changed`: the first bucket
under a new fingerprint and the `pw_scope_change_grace_buckets` after it.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.i18n.messages import MessageRef, render
from app.models.enums import PwScopeKind, PwWatchStatus


@dataclass(frozen=True)
class GroupRow:
    """The columns of `log_source_group` resolution reads."""

    qradar_id: int
    parent_qradar_id: int | None
    synced_at: datetime


@dataclass(frozen=True)
class SourceRow:
    """The columns of `log_source` resolution reads."""

    id: uuid.UUID
    qradar_id: int
    type_id: int | None
    group_ids: tuple[int, ...] = ()
    maintenance_mode: bool = False
    maintenance_until: datetime | None = None


@dataclass(frozen=True)
class ResolvedScope:
    """What a watch's scope resolved to this tick.

    `status` is None when it resolved. Otherwise it is the `PwWatchStatus` the watch takes,
    `reason` says why in words that name no value (`message` is the registry reference those
    words were rendered from), and nothing is searched.
    `chunks` are the id lists of the searches: one of one id for a source or a type, and for
    a group consecutive runs of at most `max_sources` sorted ids.
    """

    scope_kind: PwScopeKind
    status: PwWatchStatus | None = None
    reason: str | None = None
    message: MessageRef | None = None
    chunks: tuple[tuple[int, ...], ...] = ()
    member_count: int | None = None
    fingerprint: str | None = None
    #: The groups the walk met, sorted: the editor names them (§G.4).
    groups_walked: tuple[int, ...] = ()
    #: The one source of a `LOG_SOURCE` scope, for its maintenance window.
    source: SourceRow | None = None
    #: The inventory's members, for group maintenance and for the `/scope` read. Populated by
    #: the walk even when the scope did not resolve (`SCOPE_STALE`/`SCOPE_TOO_LARGE`), so the
    #: API can return what the inventory holds, marked as it is: only `resolved` scopes search
    #: or persist anything. Type scopes deliberately have none.
    members: tuple[SourceRow, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.status is None


def scope_fingerprint(qradar_ids: Collection[int]) -> str:
    """A hash of the sorted resolved ids: equal sets, equal fingerprint, whatever the order."""
    joined = ",".join(str(i) for i in sorted(set(qradar_ids)))
    return hashlib.sha256(joined.encode("ascii")).hexdigest()


def group_and_descendants(group_id: int, groups: Mapping[int, GroupRow]) -> list[int]:
    """`group_id` and every group under it, each once, in the order the walk met them.

    A visited set ends a cycle (group 1 with parent 0 and group 0 with parent 1 is the
    lab's own inventory). A group that is its own parent, or a longer loop, is met once.
    """
    children: dict[int, list[int]] = defaultdict(list)
    for row in groups.values():
        if row.parent_qradar_id is not None:
            children[row.parent_qradar_id].append(row.qradar_id)
    visited: dict[int, None] = {group_id: None}
    frontier = [group_id]
    while frontier:
        current = frontier.pop(0)
        for child in sorted(children.get(current, ())):
            if child not in visited:
                visited[child] = None
                frontier.append(child)
    return list(visited)


def chunk_ids(ids: Sequence[int], size: int) -> tuple[tuple[int, ...], ...]:
    """Consecutive runs of at most `size` ids (the IPS plan's chunking, §G.4)."""
    if size < 1:
        raise ValueError("a chunk holds at least one id")
    return tuple(tuple(ids[i : i + size]) for i in range(0, len(ids), size))


def _scope_message(name: str, **params: object) -> MessageRef:
    return MessageRef(f"pw.scope.{name}", params)


def _unresolved(
    kind: PwScopeKind, status: PwWatchStatus, message: MessageRef, **extra: object
) -> ResolvedScope:
    return ResolvedScope(
        kind,
        status=status,
        reason=render(message),
        message=message,
        **extra,  # type: ignore[arg-type]
    )


def resolve_scope(
    scope_kind: PwScopeKind,
    *,
    log_source_id: uuid.UUID | None,
    type_id: int | None,
    group_id: int | None,
    groups: Mapping[int, GroupRow],
    sources: Sequence[SourceRow],
    now: datetime,
    stale_after: timedelta,
    max_sources: int,
    max_chunks: int,
) -> ResolvedScope:
    """The scope of one watch from the inventory the caller read once."""
    if max_sources < 1 or max_chunks < 1:
        raise ValueError("a search holds at least one source and a watch at least one search")
    if scope_kind is PwScopeKind.LOG_SOURCE:
        source = next((s for s in sources if s.id == log_source_id), None)
        if source is None:
            return _unresolved(
                scope_kind, PwWatchStatus.SCOPE_EMPTY, _scope_message("source_missing")
            )
        return ResolvedScope(
            scope_kind,
            chunks=((source.qradar_id,),),
            member_count=1,
            source=source,
            members=(source,),
        )
    if scope_kind is PwScopeKind.LOG_SOURCE_TYPE:
        if type_id is None:
            return _unresolved(
                scope_kind, PwWatchStatus.SCOPE_EMPTY, _scope_message("names_no_type")
            )
        typed_members = sum(1 for s in sources if s.type_id == type_id)
        if typed_members == 0:
            return _unresolved(
                scope_kind,
                PwWatchStatus.SCOPE_EMPTY,
                _scope_message("type_empty"),
                member_count=0,
            )
        return ResolvedScope(scope_kind, chunks=((type_id,),), member_count=typed_members)
    if group_id is None:
        return _unresolved(
            scope_kind, PwWatchStatus.SCOPE_EMPTY, _scope_message("names_no_group")
        )
    own = groups.get(group_id)
    if own is None:
        return _unresolved(
            scope_kind, PwWatchStatus.SCOPE_EMPTY, _scope_message("group_missing")
        )
    walked = group_and_descendants(group_id, groups)
    wanted = set(walked)
    member_rows = tuple(
        sorted(
            (s for s in sources if wanted.intersection(s.group_ids)),
            key=lambda source: source.qradar_id,
        )
    )
    member_ids = sorted({source.qradar_id for source in member_rows})
    walked_ids = tuple(sorted(wanted))
    # The inventory facts are reported even when the scope does not resolve, so the API can
    # show what the last sync holds; `chunks` and the status still fail the scope closed.
    # Staleness is the **oldest** walked row's age, not the watch's own group: an interrupted
    # sync can leave a descendant older than the limit while the named group is fresh, and
    # `/scope` reports the oldest walked row too, so the two must agree (PW-3c item 4).
    walked_rows = [groups[gid] for gid in walked if gid in groups]
    oldest = min((row.synced_at for row in walked_rows), default=own.synced_at)
    if now - oldest > stale_after:
        return _unresolved(
            scope_kind,
            PwWatchStatus.SCOPE_STALE,
            _scope_message(
                "group_stale", synced=oldest, limit=stale_after.total_seconds()
            ),
            member_count=len(member_ids),
            groups_walked=walked_ids,
            members=member_rows,
        )
    if not member_ids:
        return _unresolved(
            scope_kind,
            PwWatchStatus.SCOPE_EMPTY,
            _scope_message("group_empty"),
            member_count=0,
            groups_walked=walked_ids,
            members=member_rows,
        )
    chunks = chunk_ids(member_ids, max_sources)
    if len(chunks) > max_chunks:
        return _unresolved(
            scope_kind,
            PwWatchStatus.SCOPE_TOO_LARGE,
            _scope_message(
                "too_large",
                sources=len(member_ids),
                searches=len(chunks),
                limit=max_chunks,
            ),
            member_count=len(member_ids),
            groups_walked=walked_ids,
            members=member_rows,
        )
    return ResolvedScope(
        scope_kind,
        chunks=chunks,
        member_count=len(member_ids),
        fingerprint=scope_fingerprint(member_ids),
        groups_walked=walked_ids,
        members=member_rows,
    )


@dataclass(frozen=True)
class ScopeRecord:
    """What a stored collected bucket says about its scope."""

    fingerprint: str | None
    scope_changed: bool


def scope_changed_flags(
    history: Sequence[ScopeRecord], fingerprint: str | None, buckets: int, grace: int
) -> list[bool]:
    """Which of the next `buckets` buckets carry `scope_changed` (design §G.4).

    `history` is the watch's stored collected buckets, newest first (`grace + 1` suffice).
    The first bucket under a fingerprint that differs from the newest stored one is flagged,
    and so are the `grace` buckets after it. A watch with no history has nothing to compare
    with: its first bucket is not a change. A scope without a fingerprint (a source or a
    type) never changes.
    """
    if buckets < 0 or grace < 0:
        raise ValueError("bucket and grace counts are not negative")
    flags: list[bool] = []
    if not history:
        return [False] * buckets
    current = history[0].fingerprint
    run = 0
    for record in history:
        if not record.scope_changed or record.fingerprint != current:
            break
        run = run + 1
    for _ in range(buckets):
        if fingerprint != current:
            current = fingerprint
            run = 1
            flags.append(True)
        elif 0 < run < grace + 1:
            run += 1
            flags.append(True)
        else:
            run = 0
            flags.append(False)
    return flags
