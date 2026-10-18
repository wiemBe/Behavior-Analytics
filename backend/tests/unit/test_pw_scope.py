"""Property-watch scope resolution (design §G.4, §G.15 "Scope"). Pure."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import PwScopeKind, PwWatchStatus
from app.property_watch.scope import (
    GroupRow,
    ScopeRecord,
    SourceRow,
    chunk_ids,
    group_and_descendants,
    resolve_scope,
    scope_changed_flags,
    scope_fingerprint,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
FRESH = NOW - timedelta(hours=1)
DAY = timedelta(hours=24)


def group(qid: int, parent: int | None = None, synced: datetime = FRESH) -> GroupRow:
    return GroupRow(qid, parent, synced)


def source(qid: int, groups: tuple[int, ...] = (), type_id: int | None = None) -> SourceRow:
    return SourceRow(uuid.uuid4(), qid, type_id, groups)


def resolve(kind, *, groups=(), sources=(), group_id=None, type_id=None, log_source_id=None,
            max_sources=50, max_chunks=4, now=NOW):  # type: ignore[no-untyped-def]
    return resolve_scope(
        kind,
        log_source_id=log_source_id,
        type_id=type_id,
        group_id=group_id,
        groups={g.qradar_id: g for g in groups},
        sources=list(sources),
        now=now,
        stale_after=DAY,
        max_sources=max_sources,
        max_chunks=max_chunks,
    )


class TestSourceAndType:
    def test_a_source_resolves_to_its_own_id(self) -> None:
        src = source(77)
        scope = resolve(PwScopeKind.LOG_SOURCE, sources=[src, source(78)], log_source_id=src.id)
        assert scope.resolved and scope.chunks == ((77,),)
        assert scope.member_count == 1 and scope.fingerprint is None
        assert scope.source == src
        assert scope.members == (src,)

    def test_a_source_missing_from_the_inventory_is_empty(self) -> None:
        scope = resolve(PwScopeKind.LOG_SOURCE, sources=[source(1)], log_source_id=uuid.uuid4())
        assert scope.status is PwWatchStatus.SCOPE_EMPTY and scope.chunks == ()

    def test_a_type_resolves_to_the_type_id_and_counts_the_synced_sources(self) -> None:
        scope = resolve(
            PwScopeKind.LOG_SOURCE_TYPE,
            sources=[source(1, type_id=7), source(2, type_id=7), source(3, type_id=8)],
            type_id=7,
        )
        assert scope.resolved and scope.chunks == ((7,),)
        assert scope.member_count == 2 and scope.fingerprint is None

    def test_a_type_is_never_chunked(self) -> None:
        many = [source(i, type_id=7) for i in range(200)]
        scope = resolve(PwScopeKind.LOG_SOURCE_TYPE, sources=many, type_id=7, max_sources=2,
                        max_chunks=1)
        assert scope.resolved and scope.chunks == ((7,),) and scope.member_count == 200

    def test_a_type_with_no_synced_source_is_empty(self) -> None:
        scope = resolve(PwScopeKind.LOG_SOURCE_TYPE, sources=[source(1, type_id=8)], type_id=7)
        assert scope.status is PwWatchStatus.SCOPE_EMPTY and scope.member_count == 0


class TestGroups:
    def test_the_members_of_the_group_itself(self) -> None:
        scope = resolve(
            PwScopeKind.LOG_SOURCE_GROUP,
            groups=[group(5)],
            sources=[source(1, (5,)), source(2, (5, 9)), source(3, (9,)), source(4)],
            group_id=5,
        )
        assert scope.resolved and scope.chunks == ((1, 2),) and scope.member_count == 2
        assert scope.fingerprint == scope_fingerprint([1, 2])

    def test_descendant_groups_count(self) -> None:
        groups = [group(1), group(2, 1), group(3, 2), group(4, 99)]
        sources = [source(10, (1,)), source(11, (2,)), source(12, (3,)), source(13, (4,))]
        scope = resolve(PwScopeKind.LOG_SOURCE_GROUP, groups=groups, sources=sources, group_id=1)
        assert scope.chunks == ((10, 11, 12),)
        assert scope.groups_walked == (1, 2, 3)
        assert [member.qradar_id for member in scope.members] == [10, 11, 12]

    def test_two_groups_naming_each_other_as_parent_end_the_walk(self) -> None:
        # The lab's own inventory: id 1 has parent 0, id 0 has parent 1.
        groups = {0: group(0, 1), 1: group(1, 0)}
        assert sorted(group_and_descendants(1, groups)) == [0, 1]
        assert sorted(group_and_descendants(0, groups)) == [0, 1]

    def test_the_walk_visits_each_group_once_and_resolves_the_union(self) -> None:
        groups = [group(0, 1), group(1, 0), group(2, 1)]
        sources = [source(10, (0,)), source(11, (1,)), source(12, (2,)), source(13, (7,))]
        scope = resolve(PwScopeKind.LOG_SOURCE_GROUP, groups=groups, sources=sources, group_id=1)
        assert scope.resolved
        assert scope.chunks == ((10, 11, 12),)
        assert scope.groups_walked == (0, 1, 2)

    def test_a_group_that_is_its_own_parent_and_a_longer_loop_terminate(self) -> None:
        assert group_and_descendants(1, {1: group(1, 1)}) == [1]
        loop = {1: group(1, 3), 2: group(2, 1), 3: group(3, 2)}
        assert sorted(group_and_descendants(1, loop)) == [1, 2, 3]

    def test_a_walk_over_a_deep_chain_is_linear(self) -> None:
        groups = {i: group(i, i - 1 if i else None) for i in range(2000)}
        assert len(group_and_descendants(0, groups)) == 2000

    def test_chunking_at_the_cap_keeps_ids_sorted_and_consecutive(self) -> None:
        sources = [source(i, (5,)) for i in range(120, 0, -1)]
        scope = resolve(PwScopeKind.LOG_SOURCE_GROUP, groups=[group(5)], sources=sources,
                        group_id=5, max_sources=50)
        assert [len(c) for c in scope.chunks] == [50, 50, 20]
        flat = [i for c in scope.chunks for i in c]
        assert flat == sorted(flat) == list(range(1, 121))

    def test_chunk_ids_refuses_an_empty_chunk(self) -> None:
        with pytest.raises(ValueError):
            chunk_ids([1], 0)
        assert chunk_ids([], 3) == ()

    def test_a_vanished_group_is_empty(self) -> None:
        scope = resolve(PwScopeKind.LOG_SOURCE_GROUP, groups=[group(1)], sources=[], group_id=9)
        assert scope.status is PwWatchStatus.SCOPE_EMPTY and scope.chunks == ()

    def test_a_group_with_no_member_is_empty_with_the_reason_named(self) -> None:
        scope = resolve(PwScopeKind.LOG_SOURCE_GROUP, groups=[group(1)],
                        sources=[source(1, (2,))], group_id=1)
        assert scope.status is PwWatchStatus.SCOPE_EMPTY
        assert scope.reason and "hold no synced log source" in scope.reason
        assert scope.member_count == 0

    def test_a_group_row_older_than_the_limit_is_stale_even_with_members(self) -> None:
        # A failed group listing is swallowed by the sync: sources keep syncing while the
        # group rows keep their old `synced_at`.
        old = NOW - DAY - timedelta(seconds=1)
        scope = resolve(PwScopeKind.LOG_SOURCE_GROUP, groups=[group(1, synced=old)],
                        sources=[source(1, (1,))], group_id=1)
        assert scope.status is PwWatchStatus.SCOPE_STALE and scope.chunks == ()
        assert scope.reason and old.isoformat() in scope.reason

    def test_a_group_row_exactly_at_the_limit_is_not_stale(self) -> None:
        scope = resolve(PwScopeKind.LOG_SOURCE_GROUP, groups=[group(1, synced=NOW - DAY)],
                        sources=[source(1, (1,))], group_id=1)
        assert scope.resolved

    def test_stale_wins_over_empty(self) -> None:
        old = NOW - 2 * DAY
        scope = resolve(PwScopeKind.LOG_SOURCE_GROUP, groups=[group(1, synced=old)], sources=[],
                        group_id=1)
        assert scope.status is PwWatchStatus.SCOPE_STALE

    def test_too_many_chunks_is_too_large_and_searches_nothing(self) -> None:
        sources = [source(i, (1,)) for i in range(201)]
        scope = resolve(PwScopeKind.LOG_SOURCE_GROUP, groups=[group(1)], sources=sources,
                        group_id=1, max_sources=50, max_chunks=4)
        assert scope.status is PwWatchStatus.SCOPE_TOO_LARGE and scope.chunks == ()
        assert scope.member_count == 201
        ok = resolve(PwScopeKind.LOG_SOURCE_GROUP, groups=[group(1)], sources=sources[:200],
                     group_id=1, max_sources=50, max_chunks=4)
        assert ok.resolved and len(ok.chunks) == 4

    def test_a_group_source_with_no_group_ids_is_in_no_group(self) -> None:
        scope = resolve(PwScopeKind.LOG_SOURCE_GROUP, groups=[group(1)],
                        sources=[SourceRow(uuid.uuid4(), 1, None, ())], group_id=1)
        assert scope.status is PwWatchStatus.SCOPE_EMPTY

    def test_the_fingerprint_ignores_order_and_duplicates_and_sees_membership(self) -> None:
        assert scope_fingerprint([3, 1, 2]) == scope_fingerprint([1, 2, 3, 3])
        assert scope_fingerprint([1, 2]) != scope_fingerprint([1, 2, 3])
        assert len(scope_fingerprint([1])) == 64

    def test_bad_limits_are_refused(self) -> None:
        with pytest.raises(ValueError):
            resolve(PwScopeKind.LOG_SOURCE_GROUP, groups=[group(1)], group_id=1, max_sources=0)


class TestScopeChange:
    def record(self, fp: str | None, changed: bool = False) -> ScopeRecord:
        return ScopeRecord(fp, changed)

    def test_no_history_is_no_change(self) -> None:
        assert scope_changed_flags([], "a", 3, 2) == [False, False, False]

    def test_the_same_fingerprint_is_no_change(self) -> None:
        assert scope_changed_flags([self.record("a")], "a", 3, 2) == [False, False, False]

    def test_a_new_fingerprint_opens_the_first_bucket_and_the_grace_after_it(self) -> None:
        flags = scope_changed_flags([self.record("a")], "b", 6, 2)
        assert flags == [True, True, True, False, False, False]

    def test_zero_grace_flags_only_the_first_bucket(self) -> None:
        assert scope_changed_flags([self.record("a")], "b", 3, 0) == [True, False, False]

    def test_a_window_already_running_continues_across_ticks(self) -> None:
        # grace 2: three buckets are flagged in all. Two are stored, one is left.
        history = [self.record("b", True), self.record("b", True), self.record("a")]
        assert scope_changed_flags(history, "b", 3, 2) == [True, False, False]
        # all three stored: the window is over
        history = [self.record("b", True)] * 3 + [self.record("a")]
        assert scope_changed_flags(history, "b", 2, 2) == [False, False]

    def test_a_change_during_the_grace_restarts_it(self) -> None:
        history = [self.record("b", True), self.record("a")]
        assert scope_changed_flags(history, "c", 4, 2) == [True, True, True, False]

    def test_a_scope_without_a_fingerprint_never_changes(self) -> None:
        assert scope_changed_flags([self.record(None)], None, 3, 12) == [False] * 3

    def test_a_flagged_bucket_under_another_fingerprint_does_not_extend_the_run(self) -> None:
        history = [self.record("b", False), self.record("a", True)]
        assert scope_changed_flags(history, "b", 2, 2) == [False, False]

    def test_negative_counts_are_refused(self) -> None:
        with pytest.raises(ValueError):
            scope_changed_flags([], "a", -1, 0)
