# UI-2.1 report — Home data layer

## 1. Files changed
- `frontend/src/hooks/useApiResource.ts` (new): the shared-request hook.
- `frontend/src/hooks/useApiResource.test.tsx` (new): 13 tests.
- `frontend/src/components/shell/AutoRefresh.tsx`: additive `useRefreshTick()` plus a tick held in `DataAgeProvider`; the timer calls `bump()`. No layout change needed.
- `frontend/src/components/shell/AutoRefresh.test.tsx`: two tests added at the end; existing tests untouched.
- `frontend/src/lib/api.ts`: optional `monitored_sources` on `FleetClassSummary` (backend sends it; the type lacked it), and `homeReads` (existing endpoints only, each `{ key, fetch(signal) }`, key = path with query).
- `frontend/src/lib/home/`: `qualifiers.ts`, `totalEps.ts`, `logSources.ts`, `offenses.ts`, `anomalies.ts`, `epsByClass.ts`, `topSources.ts`, `severityPerHour.ts`, `fixtures.ts` (test builders), and one `*.test.ts` per derivation (7 files, 61 tests).

## 2. What I did
- `useApiResource(key, fetcher)`: module-level map; same key shares one request and, while a caller is mounted, its settled result (a late caller gets the cache, no new request). A refresh tick re-runs every caller; the first starts one new request, the rest join, and the old data stays with `refreshing: true`. Abort only when the last listener leaves. States: `idle` (null key), `loading`, `ok` (data, `receivedAt` ISO, `refreshing`), `forbidden` (`httpStatus: 403`), `error` (`httpStatus` or null; no exception text). No new dependency.
- Derivations return value plus qualifiers (`INSUFFICIENT_DATA`, `PARTIAL` "n of m intervals collected", `TRUNCATED` "First n of total", `NOT_COLLECTED`). Missing values are `null` with the reason in a qualifier or gap code, never 0. Reused: `healthDistribution`, `classLabel`.
- EPS by class: five classes or fewer are not folded; more gives top 4 plus an aggregate keyed `__rest__`, labelled "Other classes" (the class `OTHER` is also labelled "Other"; reusing the word would be ambiguous). `restText` lists the contents; `unclassifiedInRest` flags UNCLASSIFIED.

## 3. Gates (from `frontend/`)
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit` printed nothing after the command echo (clean, exit 0)
- `npm test`: ` Test Files  111 passed (111)` / ` Tests  1742 passed (1742)`
- `npm run build`: succeeded (route table printed, no error)

Break-it checks (each made a test fail; code restored after each):
1. `useApiResource`: abort without checking for remaining listeners -> "does not abort while another caller is still mounted" fails.
2. `useApiResource`: always start a new request (no shared map) -> 4 sharing/refresh tests fail.
3. `useApiResource`: 403 check changed to 404 -> "reports a 403 as forbidden" fails.
4. `useApiResource`: tick removed from effect deps -> 2 refresh tests fail.
5. `totalEps`: sum with no null guard (0 instead of null) -> 2 INSUFFICIENT_DATA tests fail.
6. `offenses`: no-snapshot probe test disabled (`total === 0` -> `=== -1`) -> NOT_COLLECTED test fails.
7. `anomalies`: TRUNCATED condition forced false -> truncation test fails.
8. `topSources`: COMPLETE-bucket guard removed -> deviation guard test fails.
9. `epsByClass`: fold threshold raised -> 3 fold/Other tests fail.
10. `AutoRefresh`: `bump()` removed -> tick test fails.

## 4. Open points
Backend findings:
- **No-snapshot offenses**: `aggregate_metrics` returns zeros (active 0, critical 0, ...), identical to "snapshots exist, none open". So `offensesCard(analytics, probe)` needs `GET /offenses?limit=1` (`total === 0` means no snapshot). If the probe is missing and `active === 0`, it returns `NOT_COLLECTED` ("could not confirm"), failing closed.
- `magnitude_distribution` keys a NULL magnitude as the string `"unassigned"`; the derivation counts non-numeric keys as "unknown magnitude", separate from "below".

Plan fields that do not exist or differ:
- `FleetClassSummary.monitored_sources`: backend sends it, `api.ts` lacked it (added, optional). Fallback is `baseline_sources / baseline_share`, else coverage text omits "of m".
- Per-source measured seconds: not in `top_sources` (only `events`). EPS = events / window seconds, as the plan says, but it reads low for a source with gaps; rows carry `epsIsLowerBound` when the class window is incomplete.
- Deviation basis: `/behavior/sources.deviation_ratio` compares the latest bucket, not the 24 h mean. Rows carry `deviationBasis: "latest bucket"`; the value is null unless `last_bucket_completeness === "COMPLETE"` (field exists in backend and `api.ts`).
- `captured_at` for card 3 ("no snapshot yet" case): not returned by the derivation. `GET /offenses?limit=1` returns an arbitrary snapshot row, not the latest; it would be an unreliable "as of". Not invented.
- No previous-window figure, no fleet EPS series (as the plan states).
- `useApiResource` takes `(key, fetcher)` without the plan's `{refreshMs}` option; refresh is driven by the shell tick only (task text).
- A failed refresh replaces the shown data with the error state (honest, but the last good data is not kept on failure).
- `act(...)` warnings appear in AutoRefresh tests (the pre-existing `choose` helper triggers them; also in the new tests that reuse it).

## Review fixes (round 2)

Files changed: `frontend/src/hooks/useApiResource.ts` (+ test), `frontend/src/lib/api.ts`, `frontend/src/lib/home/totalEps.ts` (+ test), and three existing test fixtures that built a `FleetClassSummary` (`frontend/src/app/fleet/page.test.tsx`, `frontend/src/components/fleet/FleetClassTable.test.tsx`, `frontend/src/lib/fleet.test.ts`).

1. A failed refresh keeps the last good data. With an earlier `ok` answer for the key, a non-403 failure leaves `ok` with the previous `data` and `receivedAt`, `refreshing: false`, and `refreshFailed: { httpStatus }`. A 403 on refresh is `forbidden`. With no earlier answer the failure is `error`. A later success sets `refreshFailed: null`. The file's header comment describes this. New tests: keeps data and sets `refreshFailed` (also no exception text); 403 refresh gives `forbidden`; a later success clears `refreshFailed`; a first-request failure is `error`.
2. `monitored_sources` is a required `number`. The `baseline_sources / baseline_share` fallback is removed; `ExpectedCoverage.monitoredSources` is now a `number` and the "of m" text is always written.
   - Removed test (it covered only the deleted fallback): "recovers the monitored count from the share on an older response, else says nothing of it" in `totalEps.test.ts`. This is the only assertion removed.
   - Only line added to the three existing fixtures: `monitored_sources: <enabled count>`. No assertion was changed.

Gates (from `frontend/`):
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, no errors printed
- `npm test`: ` Test Files  111 passed (111)` / ` Tests  1745 passed (1745)`
- `npm run build`: ` ✓ Compiled successfully in 3.6s`, ` ✓ Generating static pages (25/25)`

Break-it checks (each failed a test; code restored):
- refresh-failure keep guard (`prev.settled?.status === "ok"` -> `false`): 2 tests fail.
- keep-previous-data branch replaced by overwriting with the error: 2 tests fail.
- `s.status === "error"` widened to `!== "ok"` (so 403 keeps stale data): the 403 test fails.
- success branch keeping the old `refreshFailed` instead of clearing it: many hook tests fail (including the clear test).
- `baselinedSources < monitoredSources` forced false: coverage test fails.
