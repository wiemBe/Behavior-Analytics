# PW-2a report

## 1. Files changed

Source (backend/app):
- `providers/dto.py`: `PwSearchKind` (GROUPED, TOTALS, PINNED), `PwKeyedRow`, `PwKeyedResult`.
- `providers/base.py`: `get_pw_keyed(...)` in the `get_ips_keyed` style (requires `AQL_EXECUTION`; MCP inherits the refusal, so `qradar_mcp.py` is untouched).
- `providers/qradar_rest.py`: `get_pw_keyed` (`LIMIT cap + 1`, padded `START/STOP`), new ctor arg `pw_max_sources_per_search` (default 50).
- `providers/mock.py`: `get_pw_keyed`, `MockPwSearch`, hooks `pw_fault`, `pw_force_truncated`, `pw_script`, deterministic synthetic events, ctor arg `pw_max_sources_per_search`. It builds the real query and uses the real parser.
- `property_watch/aql.py` (new): input validation and the three queries.
- `property_watch/keyed_rows.py` (new): shared row parser (REST and mock).
- `property_watch/keyed_search.py` (new): `PwSearchBudget`, `PwSearchPlan`, `run_search` (split driver), `combine_chunks`, outcome types.
- `property_watch/fold.py` (new): sanitizing, tuple keys, the cut, pins, `fold_bucket` producing a `PwBucketFact`.

Tests (new, backend/tests/unit): `test_pw_aql.py`, `test_pw_keyed_provider.py`, `test_pw_keyed_search.py`, `test_pw_fold.py` (190 tests).

Docs: `docs/PHASE-G-PROPERTY-WATCH.md` (only §G.4, §G.5, §G.6, §G.14.1: `[BUILT — PW-2a]` markers and as-built notes); this report.

`facts.py` and `reference.py` are untouched.

## 2. What I did, per design section

- **§G.4 (AQL side).** `scope_condition`: source `logsourceid IN (id)`, type `devicetype = id`, group `logsourceid IN (ids)` per chunk. Input is already-resolved ids; source/type take exactly one id, a group chunk at most `pw_max_sources_per_search`; ids are non-negative ints, sorted, de-duplicated. `combine_chunks`: a bucket is COMPLETE only if every chunk was whole, LOWER_BOUND if any was cut at one bucket, unmeasured if any chunk failed or was not collected (my decision: rows of the other chunks are dropped, a partial scope is not the scope).
- **§G.6 searches.** Grouped, totals, pinned exactly as written: `p0/p1/k/events` aliases, `IS NOT NULL` on each property, `GROUP BY ... ORDER BY SUM(eventcount) DESC LIMIT cap+1`. Pins use `IN ('a','b')` over the distinct, sorted components per property, never OR-ed equality. Names and pins are re-validated inside the generator via `PwFieldReference` and `validate_pinned_value`; a rejected value never reaches the text.
- **§G.6 fold.** Sanitizing (strip, non-printable becomes U+FFFD, cut with flag, whitespace-only/non-text is unparsed, never raises); two raw strings that sanitize alike are one value; U+001F tuple key; tracked and pinned values kept at any rank, untracked top `max_values` by (events desc, key); the tail goes into `untracked_events` only when the grouped search was COMPLETE. Key outside the span fails the search (parser) and a row in another bucket is refused by the fold. Counts use PW-1's `coerce_count`. Tuple width: each component is limited to `(max_length-1)//2` (127 at default) so the joined key fits the stored 255.
- **§G.5.** `total` from the totals search through `count_from_search` (a whole search with no row is a measured 0; failed/cut/not collected is `None`); `parsed` is the sum of rows whose components are all values; the remainder is never a value row. If parsed > total (searches disagree), total is left unknown.
- **Pins.** A whole grouped search is the authority; when it is cut or unmeasured, a measured pinned search fills in the pinned values (larger count wins, a LOWER_BOUND parsed is raised to cover them). A grouped-NOT_COLLECTED bucket keeps pinned values with `pinned_measurement` EXACT, so `reading_for` yields a measured zero for an absent pin. Combination pins keep only the pinned tuples.
- **§G.6 split and §G.14.1.** `run_search`: time-first halves (left first, smaller left half), multi-bucket truncation keeps nothing, one bucket truncated is LOWER_BOUND with its rows, a `ProviderError` is FAILED and not split (only the exception class is logged), rows outside the window / over the cap / wrong arity make the search FAILED, a spent budget gives NOT_COLLECTED. `PwSearchBudget(pw_max_searches_per_tick, max_per_watch=...)` also implements the per-watch cap of §G.14.1. Row caps: grouped = `max_rows`, totals = buckets of the (half) window, pinned = product of distinct pinned components × buckets, capped at `max_rows`.
- **Mock.** Same query builder and parser; `test_the_real_driver_and_fold_run_on_the_mock` runs mock → driver → fold for 12 buckets.

## 3. Gates (from `/home/efe/Documents/QRadar-Dash-wt/pw2a/backend`, main venv, `PYTHONPATH=.`)

- `ruff check app tests`: All checks passed.
- `mypy app`: Success, no issues in 252 source files (0 errors).
- `pytest -m "not integration"` whole unit suite: **4073 passed, 10 skipped, 1308 deselected**, 5 warnings (skips are the pre-existing `test_route_auth_guard.py` allowlist ones; baseline before my last four tests was 4069 passed). Log: `/home/efe/Documents/QRadar-Dash-work/pw2a/unit.log`.
- New files alone: 190 passed.
- Integration tests: not run (no database given). No QRadar access.

### Guard mutations (script `/home/efe/Documents/QRadar-Dash-work/pw2a/mutate.py`, each restored afterwards)

Every mutation below was killed; the first run had two survivors, which I fixed with new tests and re-ran.
- aql: `IS NOT NULL` dropped (golden); pinned validation dropped (`test_a_bad_pin_is_refused_without_echo[o'brien]`); type scope `=` to `IN` (golden); `IN` replaced by OR-ed equality (golden pinned tests).
- keyed_rows: `>` to `>=` at the cap (`test_truncation_is_more_than_max_rows[10-False]`); span check dropped (`test_a_key_outside_the_span_fails_the_whole_search`); strict count dropped (`test_a_malformed_count_fails_the_whole_search[12.5]`).
- keyed_search: no time split (`test_a_truncated_multi_bucket_search_keeps_nothing_and_splits_by_time`); budget ignored (mock end-to-end test); containment dropped (`test_a_row_outside_the_searched_window_fails_the_search`); chunk LOWER_BOUND dropped (`test_chunk_sums_are_exact_only_when_every_chunk_was_whole`); chunk NOT_COLLECTED dropped (`test_a_chunk_without_a_measurement_makes_the_bucket_unmeasured`); per-watch cap dropped (`test_the_per_watch_cap_...`).
- fold: tail summed when cut (`test_the_tail_is_summed_into_untracked_only_when_the_search_was_whole`); tracked not kept (`test_a_tracked_value_is_kept_whatever_its_rank`); cut too wide (`test_untracked_values_are_kept_only_inside_the_cut`); no sanitization (`test_replaces_unprintable_characters`); unmeasured totals read as zero (survived at first because the `parsed > total` guard masked it; new `test_an_empty_result_that_was_not_whole_is_not_a_zero_total` kills it); pinned overriding a whole grouped search (`test_a_whole_grouped_search_stays_the_authority_for_counts`); disagreeing searches (`test_searches_that_disagree_leave_coverage_unknown`); tuple width (`test_component_limit_...`); unparsed row becoming a value; row arity; pin/outcome pairing; unpinned cross terms kept (`test_a_combination_keeps_only_the_pinned_tuples`); pinned max (survived at first, now `test_the_pinned_count_never_lowers_what_the_grouped_search_saw`).
- Provider (by hand): `require(AQL_EXECUTION)` removed (`test_mcp_does_not_support_it`); `LIMIT cap+1` to `cap` (three `TestAql` tests); mock NULL filter removed (`test_grouped_rows_are_never_null...`, `test_totals_cover_the_grouped_events`).

## 4. Open points

1. **Hypothesis is not installed and not used anywhere in the repo.** The task said "Hypothesis, as the repository already uses"; the repo uses seeded `random.Random` loops (e.g. `test_monotonicity_property`). The hostile-input tests (`TestHostileInput`, `test_hostile_input_never_raises`) are seeded random property tests of 2,000-3,000 cases. If something real needs Hypothesis, it must be added to the dependencies (not my files).
2. **`providers/factory.py` does not pass `pw_max_sources_per_search`** (not in my allowed files). Until PW-2b wires it, the provider cap defaults to 50, which equals the setting's default; if an operator changes the setting, the driver (`max_sources=`) enforces the new value but the provider would still refuse chunks above 50.
3. **Circular import.** `property_watch.reference` imports `providers.base`, and the `providers` package imports `mock.py`/`qradar_rest.py`, so those two import `property_watch.aql`/`keyed_rows` inside `get_pw_keyed`. Importing `app.property_watch.keyed_rows` or `keyed_search` as the very first import in a fresh interpreter fails through `app.collectors.bucketed_metrics`; that is the same pre-existing fragility `app.ips.keyed_*` has (any import of `app.providers` first fixes it, and pytest and the app always do).
4. **Partial-chunk rule is stricter than the design text.** §G.4 only says a bucket is LOWER_BOUND when a chunk's tail is missing; I make a bucket unmeasured when a whole chunk failed or was not collected. Risk if left: a group with one persistently failing chunk never produces a bucket (gap, not a wrong number). PW-2b/the orchestrator should confirm.
5. **Searches that disagree** (`parsed > total` in the same bucket) leave the total unknown rather than raising. Risk: a systematic cause (clock skew between searches) would show as permanently unknown coverage; it would be visible as `totals_complete = false`.
6. **Admission columns of `PwBucketFact`** (`observed_values`, `tracked_values_observed`, ...) keep their defaults; PW-2b fills them. `query_provenance` strings are on `PwBucketOutcome.provenance.query` for PW-2b to persist.
7. **Not tested:** a real console (no QRadar access) and PostgreSQL. The AQL shape follows §G.6 and PW-0's findings but was only checked against golden strings and a mocked HTTP layer.
8. The mock models a type scope as one pseudo source (it has no inventory), so mock type-scope data is synthetic per type id.
