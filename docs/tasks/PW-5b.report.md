# PW-5b report — Phase G API, the scope half

STATUS: FINAL

## 1. Files changed

PW-5b is one delivery: the inherited snapshot already carried the code, and this session reviewed
it, fixed four things, and added the missing tests. The files of the task are below; the ones this
session changed are marked **(this session)**.

- `backend/app/api/routes/property_watches.py`: the inherited `GET /{watch_id}/scope` route,
  `_resolved_options` (a resolver count is reported only when it resolved, or when an empty scope
  measured a real zero), and the `property-options` group branch that takes the in-scope types from
  the resolver's walk (404 for an absent group row, 409 for one that does not resolve). Reviewed,
  correct, unchanged in this session.
- `backend/app/services/property_watches.py`: **(this session)** `_member_names` now fetches exactly
  `SCOPE_MEMBERS_LIMIT` rows and no longer special-cases a group with no members, so a group read
  costs the same statements whatever it resolved to; the rest (inherited) is `ScopeSubject`,
  `resolve_subjects` (PW-2b's resolver over the repository's one bulk read), `watch_scope`,
  `_walked_groups`, `_scope_changes`, and the minimal `resolve_groups`/`_resolution`/
  `_children`/`_descendants` **deleted** in favour of the shared resolver in
  `static_costs`, `_scope_summaries` and `preview_scope_ids`.
- `backend/app/schemas/property_watch.py`: **(this session)** the `WatchScopeOut.groups_walked`
  comment corrected (the walk is sorted by id, not walk order). Otherwise inherited:
  `WatchScopeOut`, `ScopeGroupOut`, `ScopeChangeOut`, `ScopeSummaryOut.member_count`,
  `ScopeOptionOut.scope_state`/`scope_reason`.
- `backend/app/property_watch/scope.py`: inherited, reviewed: the group walk is computed before the
  freshness check, so a `SCOPE_STALE`/`SCOPE_TOO_LARGE` scope still carries the inventory's
  `groups_walked`, `member_count` and `members` (§G.4 wants the API to show them, marked). The
  collector only acts on `resolved`/`chunks`, so its behaviour is unchanged.
- `backend/tests/integration/test_property_watches_api.py`: **(this session)** the membership-history
  seed strengthened (the skipped `NOT_COLLECTED` bucket now carries its own fingerprint, so keeping
  it invents a change) and four new tests: an empty group (`SCOPE_EMPTY` with a measured 0 and the
  fixed statement cost), a vanished group (no count, never a zero), `SCOPE_TOO_LARGE` in
  `scope-options`, and the `property-options` 409. Plus the operator-path statement pin for
  `scope-options` with no `instance_id`. The inherited PW-5b tests (source/type/group `/scope`,
  the lab's two-group cycle, `SCOPE_STALE`, the 200-name cap, the history, the cap/preview/count
  agreement, the size pins) are kept as they were.
- `backend/tests/unit/test_property_watch_api.py`: inherited, reviewed: the old `_children`/
  `_descendants` tests moved to PW-2b's shared `group_and_descendants` (same assertions, no
  weakening), the new route added to the route list, and the schema-default test that a missing
  member count stays `None`.
- `docs/PHASE-G-PROPERTY-WATCH.md`: **(this session)** the `[BUILT — PW-5b]` note in §G.11 only;
  its statement-count sentence now reads "a fixed 6 for a group … `scope-options` 3 for groups and
  2 for types … one more when the caller does not name the instance".
- `docs/tasks/PW-5b.report.md`: this report, rewritten from the inherited skeleton.

## 2. What I did

Read `AGENTS.md`, `docs/tasks/PW-5b.md`, §G.4, §G.11, §G.12, PW-2b's report and PW-5's report
(`git show agent/pw5:docs/tasks/PW-5.report.md`), then checked the inherited snapshot
(`wip(pw5b): snapshot …`, HEAD) line by line against them.

The design is right and is kept: one resolver (PW-2b's pure `resolve_scope`) over the repository's
one bulk read (`resolve_subjects`), used by `/scope`, `scope-options`, `property-options`, the
enable caps (`static_costs`/`enable_reasons`), the detail read and the preview, so none of them can
disagree about a scope. The API fails closed: a stale scope still returns what the last sync held,
marked; a vanished group returns no count; a `NOT_COLLECTED` bucket says nothing about membership.

What was wrong or missing in the inherited work, and what I did:

1. **The 200-name cap was not a pinned guard.** `_member_names` fetched `SCOPE_MEMBERS_LIMIT + 1`
   rows and sliced back to 200, but nothing read the overflow (the true total comes from the
   resolver's `member_count`). A mutation raising the `LIMIT` survived the test. Now the query
   fetches exactly the cap, so raising it returns more names and the test fails; the visible
   truncation half (`names[:LIMIT]`) is gone with it.
2. **The inherited note claimed a fixed 6 statements for a group, but an empty group cost 5.**
   `_member_names` returned early when the walk found no member. The early return is gone; an empty
   group filters to an empty `IN` list, still one statement. A test now pins the empty group's cost
   at 6 and the note says 6.
3. **`WatchScopeOut.groups_walked` was documented as "the group itself first in the walk order"**
   while both it and PW-2b's `ResolvedScope.groups_walked` are sorted by `qradar_id`. Comment
   corrected.
4. **The membership-history test did not pin the `NOT_COLLECTED` skip.** The seeded non-collected
   bucket carried the same fingerprint as its neighbours, so a read that kept it produced the same
   two changes. The bucket now carries its own fingerprint, so keeping it invents a third change
   and the test fails.
5. **Missing coverage** for the exit paths the task lists: an empty group, a vanished group, the
   `SCOPE_TOO_LARGE` state in `scope-options`, and the `property-options` 409 for a group that does
   not resolve. Added, one test each.
6. **The operator's own `scope-options` call (no `instance_id`) was unpinned.** Added a pin that it
   costs exactly one statement more than the explicit-instance read, still flat in size.

I also confirmed: no `resolve_groups`/`GroupResolution`/`_descendants`/`_children` remains anywhere
(grep), the new route is on the router's mount guard, the doc has exactly one `[BUILT — PW-5b]`
marker, and `alembic`/models were not touched (no schema change).

## 3. Gates

All commands ran from `backend/` with `PYTHONPATH=.` and the main checkout's virtualenv. Logs are
under `/home/efe/Documents/QRadar-Dash-work/pw5b/`.

- `ruff check app tests` — **All checks passed!**
- `mypy app` — **Success: no issues found in 265 source files** (0 errors).
- Whole unit suite (`-m "not integration" -ra`), log `unit-final.log` — **4,318 passed, 10 skipped,
  1,388 deselected, 6 warnings in 101.71 s**. The 10 skips are the existing route-auth allowlist
  cases (`tests/unit/test_route_auth_guard.py:255` ×7, `:265` ×3).
- PW-5b's integration file alone (`tests/integration/test_property_watches_api.py`), log
  `pw-file3.log` — **42 passed** in 91.82 s (28 inherited + 10 inherited PW-5b + 4 new).
- Whole integration suite (`-m integration tests/integration/ -ra`, one session over
  `tests/integration/`, log `integ-final.log`, `EXIT=0`) — **1,388 passed, 2 deselected, 12
  warnings in 1939.54 s (0:32:19)**. No failures and no skips; the 2 deselected are the
  configured `-m integration` deselection. This is the one whole-suite run a review needs: PW-5b
  did not perturb the tests that run after it.
- Statement-count pins (in `test_the_scope_read_costs_the_same_statements_at_every_size` and
  `test_scope_options_costs_the_same_statements_at_every_size`), identical at 2 and 20 groups and
  at 10 and 200 sources:
  - `GET /{id}/scope`: **6** statements for a group, **4** for a type, **4** for a source.
  - `GET /scope-options` (an explicit `instance_id`): **3** for groups, **2** for types, **1** for a
    page of sources. The editor's call (no `instance_id`) adds `_default_instance`'s one statement:
    **4/3** (`test_catalog_scope_options_and_property_options`).

## 4. Guard mutations

Script `/home/efe/Documents/QRadar-Dash-work/pw5b/mutate.py`, log `mutations2.log`. Each mutation
is applied to exactly one occurrence of an exact string, runs only the named test, then restores
the file from the bytes read before the edit and verifies the file is byte-identical. **All 16
mutations were CAUGHT** (the named test failed). `git status` after the run shows only my intended
edits, and the `app/` files match what is reported in §1.

| Mutation | Test that failed |
|---|---|
| mount guard removed from the `property-watches` mount (`app/api/router.py`) | `test_every_property_watch_route_without_read_is_403` |
| `_resolved_options`: every scope measurable (a stale group shows its count) | `test_scope_options_show_a_stale_group_without_a_count` |
| the same, for a too-large group | `test_scope_options_show_no_count_for_a_group_that_is_too_large` |
| member-name `LIMIT` raised by 100 | `test_the_scope_read_names_at_most_200_members_and_totals_the_rest` |
| inventory age is the newest walked `synced_at` instead of the oldest | `test_a_stale_group_inventory_is_marked_and_carries_no_zero` |
| history keeps a `NOT_COLLECTED` bucket | `test_the_scope_read_reports_the_seeded_membership_changes` |
| history drops the `watch_version` filter | same |
| history returned oldest-first | same |
| `in_grace_window` always false | same |
| `members_total` is the number of names, not the resolver's count | `test_the_scope_read_names_at_most_200_members_and_totals_the_rest` |
| a stale scope carries no inventory (`member_count=None`, `members=()`) | `test_a_stale_group_inventory_is_marked_and_carries_no_zero` |
| the resolver's bulk read repeated per subject (a query per subject) | `test_the_scope_read_costs_the_same_statements_at_every_size` |
| detail reads skip the member count (`with_members=False`) | `test_the_source_scope_takes_its_instance_and_a_duplicate_name_is_409` |
| `property-options`: no 404 for an absent group | `test_catalog_scope_options_and_property_options` |
| `property-options`: no 409 for a group that does not resolve | `test_property_options_refuse_a_group_that_does_not_resolve` |

An earlier run also mutated `.limit(SCOPE_MEMBERS_LIMIT + 1)` to `+ 2` and it **survived** — that
is finding 1 above: the inherited `+1` was dead, which is why the code now uses the cap itself.

## 5. Open points

1. **No real QRadar console.** The integration path uses the mock provider; `/scope` itself is a
   database read, but the count agreement with the preview is exercised on the mock. PW-10 owns
   live validation. If a console's group membership differs from the synced inventory, the watch
   measures the synced inventory, as §G.4 says it must.
2. **The stale decision uses the watch's own group row, while `/scope` reports the oldest walked
   row's age.** One sync pass stamps every row with the same time, so these agree — unless a pass
   was interrupted, when a descendant can keep an older `synced_at`. Then `/scope` could show an
   age above `pw_scope_stale_after_hours` with `state: null` (resolved). The fail-closed side would
   be to decide staleness on the oldest walked row; that is PW-2b's resolver rule and changing it
   moves the collector, so I did not. What could go wrong: a resolved scope whose member list is
   stale is shown without a `SCOPE_STALE` marker.
3. **`SCOPE_HISTORY_BUCKETS = 500`** is the bound I chose (the spec fixes none) for the buckets a
   `/scope` read walks back per watch. A membership change older than the newest 500 collected
   buckets of the current version is not in `changes`. What could go wrong: a very old change is
   missing from the history; the buckets route still shows the buckets.
4. **`property-options` for a group costs 3 statements** (an existence read, then the resolver's
   2), against the resolver's 2 alone. Constant, but this route's statement count is not pinned —
   only its 404/409 behaviour is. What could go wrong: a later edit adds a per-source read here
   without a test failing.
5. **Two membership-history readers now exist**: the collector's `PropertyWatchRepository.scope_history`
   (keyed by `watch_ids`, with a `since` bound) and the API's `_scope_changes` (one watch, current
   version). They agree on the rules (skip `NOT_COLLECTED`, newest-first, fingerprint change). What
   could go wrong: a future change to one and not the other makes the API and the collector report
   different histories.
6. **`changes` is bounded by 500 entries but not by a query parameter.** A watch whose scope changed
   in every one of the last 500 buckets returns ~500 entries. What could go wrong: a large response
   for a pathological watch; the UI must paginate or ignore.
7. Inherited from PW-5, unchanged: `DELETE` does not yet resolve open alerts (PW-4) and `PUT /pins`
   does not report dependent F.1 rules (PW-6).
8. The tests for the resolver walk are unit tests that call `group_and_descendants` directly (a
   helper), not through the route; the route paths are covered by the integration tests above.
   What could go wrong: the helper could change semantics without the unit test noticing, but the
   integration cycle/descendant tests would still catch a wrong walk.
