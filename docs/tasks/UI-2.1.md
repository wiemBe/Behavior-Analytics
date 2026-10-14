# Task UI-2.1 — Home data layer: `useApiResource` and the pure Home derivations

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a (the frontend quality
bar) above all**, and `docs/PRODUCT-UX-GUIDELINES.md`. This task draws nothing: it builds the data
layer the Home widgets (UI-2.2 to UI-2.5) will use. Its quality bar is honesty: **a missing
measurement is `null` with a reason, never `0`**, a capped list says it is capped, and a partial
window says how partial.

## Binding design

[`docs/UI-REDESIGN-PLAN.md`](../UI-REDESIGN-PLAN.md):
- **UR.4.2** (the four KPI cards, card by card), **UR.4.3** (EPS by source class, top sources,
  anomaly severity, log-source health), **UR.4.4** (the state table);
- **UR.5.4** (the `WidgetState` union and its qualifiers, and the paragraph on data fetching:
  `useApiResource`, the shared in-flight map, abort on unmount, refresh from the top bar);
- **UR.8.3** (why the dedupe matters: about 9 distinct requests for the default Home).

Where the plan names a field, check it exists in `frontend/src/lib/api.ts` (and, if in doubt, the
backend schema). If a field the plan relies on does not exist, do not invent it: write the
derivation for what exists and list the gap in the report.

## Build

### 1. `frontend/src/hooks/useApiResource.ts` (new)

`useApiResource<T>(key: string | null, fetcher: (signal: AbortSignal) => Promise<T>, options?)`.
- `key` is the request's identity (the path with its query). `null` means "do not fetch" (a
  widget waiting for an input).
- **Shared in-flight map** at module scope: concurrent callers with the same key share **one**
  request and one result. A caller that unmounts stops listening; the shared request is aborted
  only when **no** caller is left.
- Returns a small discriminated state: loading; ok with the data and the time it was received;
  error; and **forbidden** for an `ApiError` with status 403 (carry the status so a widget can say
  "Your role cannot read …"). Never expose an exception's text as the message.
- **Refresh**: re-fetch when the shell's auto-refresh fires. The top bar (`components/shell/
  AutoRefresh.tsx`) refreshes with `router.refresh()`, which does not reach client hooks. Add,
  additively, a refresh tick to its context (for example `useRefreshTick()`), incremented on every
  automatic refresh, and have `useApiResource` re-fetch when it changes. Keep the last good data
  visible while a refresh is in flight.
- No new dependency (no SWR, no React Query).

### 2. `frontend/src/lib/home/*.ts` (new): pure derivations

Pure functions from API responses to what a widget shows. Each returns the value **and** its
qualifiers (`INSUFFICIENT_DATA`, `PARTIAL` with "n of m", `TRUNCATED` with "first n of total",
`NOT_COLLECTED`) as the plan defines them. Split the files sensibly (one per card or widget, or a
few grouped files). At least:

- **Total EPS** (UR.4.2 card 1, from `FleetSummary`): the sum of non-null class `mean_eps`; the
  expected sum over classes with `baseline_sources > 0` and its coverage ("baselines cover n of m
  sources"); the top class and its share; `INSUFFICIENT_DATA` when every class mean is null;
  `PARTIAL` with the interval counts. No percentage when expected is null.
- **Log sources** (card 2): "n of m judged normal", where judged excludes `INSUFFICIENT_DATA`;
  the health groups (reuse `healthDistribution()` if it exists rather than re-deriving it); the
  silent count from the anomaly summary, with its definition.
- **Offenses** (card 3, from the offense analytics): open, critical by the product's
  `critical_magnitude` threshold, unassigned; the "no snapshot yet" case as `NOT_COLLECTED`, not
  zeros. Read the backend to see what the analytics return when no snapshot exists, and say what
  you found in the report.
- **Anomalies** (card 4): open; the breakdown by type; the breakdown by severity from the active
  list, **`TRUNCATED`** when the list's `total` exceeds what was returned; the "n sources not
  judged" companion.
- **EPS by source class** (UR.4.3): top 4 plus "Other"; null classes left out and counted;
  `UNCLASSIFIED` named, and if it falls into "Other" the result says what "Other" contains.
  Never more than five entries.
- **Top sources** (UR.4.3): the global top five from the union of each class's `top_sources`, EPS
  as events over the window's seconds, joined to `/behavior/sources` for the deviation (null → no
  value, never 0) and the state word.
- **Anomaly severity per hour** (UR.4.3): counts per (hour, severity) over a range;
  `TRUNCATED` when capped.

Put the numbers' formatting where the existing `lib/fleet.ts` and `lib/behavior*.ts` helpers
already do it: reuse them, do not duplicate them.

## Tests (Vitest)

- `useApiResource`: two callers with one key send **one** request; a third caller after it settles
  sends a new one only on refresh; unmounting one of two callers does not abort; unmounting the
  last one aborts; a 403 gives the forbidden state; another failure gives the error state without
  the exception text; a refresh tick re-fetches and keeps the old data visible meanwhile; a `null`
  key sends nothing.
- Every derivation: a normal case; **every null-not-zero case** (a missing mean, a missing
  expected, a missing deviation, no snapshot); each qualifier (`INSUFFICIENT_DATA`, `PARTIAL`,
  `TRUNCATED`, `NOT_COLLECTED`); the five-entry cap and "Other"; ties.
- The additive refresh tick in `AutoRefresh.tsx`: its existing tests stay green and unchanged; add
  one for the tick.
- Break the code on purpose for at least five guards and confirm a test fails; list them.

## Allowed files

- `frontend/src/hooks/useApiResource.ts` (new) and its test
- `frontend/src/lib/home/*.ts` (new) and their tests
- `frontend/src/components/shell/AutoRefresh.tsx` (the additive tick only) and its test (add only)
- `frontend/src/lib/api.ts`: only to add a missing type or a read method for an existing endpoint
- `docs/tasks/UI-2.1.report.md` (new)

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npm test`, `npm run build`. Copy each summary line verbatim into the report. There is no page to
screenshot in this task.

Write `docs/tasks/UI-2.1.report.md` in the AGENTS.md §6 format. Do not commit.
