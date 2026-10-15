# UI-5.8 report: `/config-changes`, every loading and error boundary, `.nowrap`

## 1. Files changed

- `frontend/src/components/boundary/BoundaryLoading.tsx` (new): `BoundaryLoading` and `BoundaryError`. Each composes the route's real `PageHeader` with `LoadingState` / `ErrorState` from `components/ui/RouteState.tsx`. No skeleton.
- `frontend/src/components/boundary/boundary.css` (new): three padding-top variants (`small` sp-2, `medium` sp-5, `large` sp-6), tokens only, so the loading line starts where the loaded first block starts.
- `frontend/src/components/boundary/BoundaryLoading.test.tsx` (new): tests for both components.
- 30 existing `loading.tsx` / `error.tsx` files modified (15 loading, 15 error; the root pair included): now use the shared components with the route's real title.
- 22 new boundary files (11 routes x loading and error) and 22 new tests, for `log-sources`, `log-sources/[id]`, `searches`, `searches/[id]`, `alerts`, `alerts/[id]`, `offenses`, `offenses/[id]`, `rules`, `rules/[id]`, `coverage`. Plus `frontend/src/app/error.test.tsx` (new, root error boundary).
- `frontend/src/app/config-changes/page.tsx`: `PageHeader` h1 "Configuration changes" and one honest notice.
- `frontend/src/app/config-changes/page.test.tsx`: authorised assertion edits only (section 7).
- `frontend/src/app/globals.css`: `.nowrap` defined unconditionally; the media query narrowed to `td.num.cell-when.nowrap`. Nothing else touched.
- `frontend/src/app/nowrapContract.test.ts` (new): CSS contract test.
- `docs/tasks/UI-5.8.report.md`: this file.
- Not touched: `components/ui/*`, `tokens.css` (no new token), `backend/`, any other `page.tsx`.

## 2. What I did

- All 26 loading and 26 error boundaries (the 11 new routes included) use the shared components. A grep finds no `boundary-bar`, `boundary-row`, skeleton or shimmer outside tests that assert their absence.
- `/config-changes`: `backend/app/models/config_change.py` holds the model; nothing else in `backend/app` references it except the `models/__init__.py` export. No route serves it and no collector fills it. The page says exactly that, with no phase number, link, button or table.
- `.nowrap` is now one unconditional rule; the narrow-viewport fold applies only to `td.num.cell-when` without `.nowrap`.
- In this review: fixed three offsets that missed the 4 px rule (two source-detail routes 8 px off, the root 16 px off), re-captured the affected routes on a fresh build, ran the gates, ran the break-its.

## 3. Gates

From `frontend/`:

- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit` exited 0 with no output
- `npx vitest run --no-cache`: `Test Files  221 passed (221)` / `Tests  2720 passed (2720)`. Run twice (before and after my offset edits). The `HomeDashboard` "layout saving" flake (T-112) did not occur.
- `npm run build`: `✓ Compiled successfully in 6.7s`, `✓ Generating static pages (25/25)`, exit 0.

Break-it (each restored afterwards; the same 9 test files re-ran green, 43 tests):

- Boundary: replaced `<LoadingState>` in `BoundaryLoading.tsx` with a `.boundary-bar` element. Failed: `BoundaryLoading.test.tsx` (2 tests), `alerts/loading.test.tsx` (2), `alerts/[id]/loading.test.tsx` (2).
- `.nowrap`: renamed the rule to `.nowrapx` in `globals.css`. Failed: `nowrapContract.test.ts` "is defined outside every media query".
- `/config-changes`: changed the copy to "are planned for Phase 3." Failed: "renders the heading, the phase and the scaffold explanation" and "promises nothing and names no phase".

## 4. Defect list

| Route | Defect | Rule | Fix |
|---|---|---|---|
| 11 data routes (log-sources, searches, alerts, offenses, rules, coverage, and their details) | No `loading.tsx` / `error.tsx` | spec Part 2.2 | added, with tests |
| `/` | Loading was a bare `<p>` with no heading; error a bare `<p role="alert">` with no retry | guidelines §8 | `BoundaryLoading` / `BoundaryError`, `reset` or a reload fallback |
| older boundaries | No `h1`, so the title appeared only after load | layout stability (ruling) | route's `PageHeader` in both states |
| `/admin/ips-sources/[id]`, `/admin/waf-sources/[id]` | Loading line 8 px above the loaded first block (found in this review) | ruling, 4 px | `offset="small"`; now 0 px |
| `/` | Loading line 16 px above the dashboard's first block (found in this review) | ruling, 4 px | `offset="medium"` in source; see section 9 |
| `globals.css` | `.nowrap` only inside `@media (max-width: 1200px)` | spec Part 3 | one unconditional rule |
| `/config-changes` | "Planned for Phase 3." and a promise of arrival | D-11, honesty | new copy |

## 5. `h1` and first-block top (px), loading against loaded

Source: `logs/after-measurements.json` (full run) and `logs/after-admin-ips-source-admin-waf-source-root-admin-classification-measurements.json` (re-run after the offset fixes), both taken in this review on a fresh build, in `/home/efe/Documents/QRadar-Dash-work/ui58/logs/`. "Loaded first" is the top of the loaded page's first block; "loading line" is the top of the `LoadingState` notice. Values at 1440x900. 1280x800 is identical, and 1024x768 has `h1` at 80 with the same deltas. The error boundary's `h1` and first top equal the loading values on every route. The `h1` never moves, on any route or viewport, and no page overflows horizontally in any of the three states.

The inherited run compared the wrapper `div`'s top, which hid the padding; I measure the notice's own top.

| Route | `h1` loaded / loading / error | Loaded first | Loading line | Delta |
|---|---|---|---|---|
| `/` (see section 9) | 88 / 88 / 88 | 200 | 184 (synthetic) | -16 |
| `/admin/classification-rules` | 88 / 88 / 88 | 202 | 200 | -2 |
| `/admin/ips-sources` | 88 / 88 / 88 | 184 | 184 | 0 |
| `/admin/ips-sources/[id]` | 88 / 88 / 88 | 192 | 192 | 0 |
| `/admin/outage-policy` | 88 / 88 / 88 | 184 | 184 | 0 |
| `/admin/search-cadence` | 88 / 88 / 88 | 184 | 184 | 0 |
| `/admin/waf-sources` | 88 / 88 / 88 | 184 | 184 | 0 |
| `/admin/waf-sources/[id]` | 88 / 88 / 88 | 192 | 192 | 0 |
| `/alerts` | 88 / 88 / 88 | 194 | 192 | -2 |
| `/alerts/[id]` | 88 / 88 / 88 | 184 | 184 | 0 |
| `/anomalies` | 88 / 88 / 88 | 184 | 184 | 0 |
| `/behavior` | 88 / 88 / 88 | 194 | 192 | -2 |
| `/behavior/waf` | 88 / 88 / 88 | 194 | 192 | -2 |
| `/behavior/waf/sources/[id]` | 88 / 88 / 88 | 205 | 208 | +3 |
| `/coverage` | 88 / 88 / 88 | 194 | 192 | -2 |
| `/fleet` | 88 / 88 / 88 | 194 | 194 | 0 |
| `/fleet/catchall` | 88 / 88 / 88 | 223 | 223 | 0 |
| `/log-sources` | 88 / 88 / 88 | 194 | 192 | -2 |
| `/log-sources/[id]` | 88 / 88 / 88 | 184 | 184 | 0 |
| `/offenses` | 88 / 88 / 88 | 194 | 192 | -2 |
| `/offenses/[id]` | 137 / 137 / 137 | 233 | 233 | 0 |
| `/rules` | 88 / 88 / 88 | 194 | 192 | -2 |
| `/rules/[id]` | 137 / 137 / 137 | 233 | 233 | 0 |
| `/rules/statistics` | 88 / 88 / 88 | 184 | 184 | 0 |
| `/searches` | 88 / 88 / 88 | 194 | 192 | -2 |
| `/searches/[id]` | 88 / 88 / 88 | 192 | 192 | 0 |

## 6. `.nowrap` table widths (table width / container, px)

Source: `logs/nowrap-before.json` and `logs/nowrap-after.json` (21 routes, 40 tables, 1440x900 and 1280x800). Before and after are identical on every table, so nothing newly overflows.

| Route and table | 1440 before | 1440 after | 1280 before | 1280 after |
|---|---|---|---|---|
| `/alerts` | 1144 / 1144 | 1144 / 1144 | 984 / 984 | 984 / 984 |
| `/alerts/[id]` delivery history | 1129 / 1129 | 1129 / 1129 | 969 / 969 | 969 / 969 |
| `/behavior` sources needing attention | 1129 / 1129 | 1129 / 1129 | 984 / 969 | 984 / 969 |
| `/behavior/waf/sources/[id]` | 1144 / 1144 | 1144 / 1144 | 984 / 984 | 984 / 984 |
| `/fleet/sources` | 1144 / 1144 | 1144 / 1144 | 969 / 969 | 969 / 969 |
| `/fleet/catchall` | 1129 / 1129 | 1129 / 1129 | 969 / 969 | 969 / 969 |
| `/log-sources` | 1144 / 1144 | 1144 / 1144 | 984 / 984 | 984 / 984 |
| `/offenses` | 1129 / 1129 | 1129 / 1129 | 969 / 969 | 969 / 969 |
| `/offenses/[id]` history | 1129 / 1129 | 1129 / 1129 | 969 / 969 | 969 / 969 |
| `/rules` | 1129 / 1129 | 1129 / 1129 | 969 / 969 | 969 / 969 |
| `/rules/[id]` history | 1129 / 1129 | 1129 / 1129 | 969 / 969 | 969 / 969 |
| `/searches/[id]` (2 tables) | 1129 / 1129 | 1129 / 1129 | 969 / 969 | 969 / 969 |
| `/admin/search-cadence` | 1129 / 1129 | 1129 / 1129 | 969 / 969 | 969 / 969 |
| `/admin/outage-policy` | 1129 / 1129 | 1129 / 1129 | 969 / 969 | 969 / 969 |
| `/admin/waf-sources`, `/admin/ips-sources` | 1144 / 1144 | 1144 / 1144 | 984 / 984 | 984 / 984 |
| `/admin/{waf,ips}-sources/[id]` profile history | 848 / 848 | 848 / 848 | 848 / 848 | 848 / 848 |

One overflow, pre-existing and not hidden: `/behavior` "Sources needing attention" at 1280 is 984 wide in a 969 container, identical before and after. I did not change it.

## 7. Conflict and ruling

The spec's Part 2 asked loading states to "mirror the first block's shape" with "one skeleton style". `docs/PRODUCT-UX-GUIDELINES.md` §8 says boundaries are built from `RouteState.tsx`, in plain text, with no spinner and no skeleton. AGENTS.md says to stop and report such a conflict, and Codex did. The orchestrator's ruling (`/home/efe/Documents/QRadar-Dash-work/ui58/ruling.md`) made §8 win. Loading is the real `PageHeader` plus `LoadingState`; error is `PageHeader` plus `ErrorState`; layout stability is measured (`h1` fixed, loading line within 4 px of the loaded first block); `config-changes/page.test.tsx` keeps only the authorised edits.

`config-changes/page.test.tsx` edits (authorised): heading assertion from level 2 "Configuration Changes" to level 1 "Configuration changes"; "Planned for Phase 3." to "Configuration changes are not collected."; the "scaffolded ... Phase 3" regex to `/no collector or API fills it, so this page has nothing to show\./`; the assertion on "the view is implemented in that phase ..." removed with the old copy. Test titles and comments are unchanged (the first test's title still says "the phase and the scaffold explanation"). One new test added below the existing ones ("promises nothing and names no phase"). `git diff -- '*.test.*'` shows no other existing test touched.

## 8. Inherited from Sonnet and Codex

- Kept from Codex: the `BoundaryLoading` / `BoundaryError` composition, the 11 new routes and tests, the `/config-changes` page and test edits, the `.nowrap` rule and contract test, the capture and `nowrap.mjs` scripts.
- Kept from Sonnet: the fixture, the capture approach, the before baseline.
- Reverted by Codex before I started: Sonnet's skeleton CSS and bars. `boundary.css` now holds only padding variants; no skeleton markup remains.
- Changed by me: `offset="small"` on the two `[logSourceId]` loading routes; `offset="medium"` on the root; the measurement metric (notice top, not wrapper top).
- Reverted by me: nothing. No existing test was edited beyond the authorised `config-changes` changes.

## 9. Open points

- **Root route.** `/` is a client-rendered dashboard whose real loading boundary does not show in headless capture, so `capture.mjs` injects the boundary markup into the page. The root's row in section 5 is therefore synthetic and ignores `offset`. The source now has `offset="medium"` (16 px), which should match the 200 px loaded first block at 1280 and wider, but I did not verify that in a browser. At 1024x768 the loaded first block sits at 258 px (the dashboard toolbar stacks), so no static offset matches there. Known deviation.
- **Error states are injected too.** Next's error boundary cannot be forced in the capture, so `capture.mjs` writes equivalent markup for the error PNGs. The `error.tsx` files are covered by their Vitest tests (heading, `role="alert"`, text, `reset`).
- `/behavior/waf/sources/[id]` is +3 px, inside tolerance.
- Backend untouched; `mypy` not run (backend only).

## 10. Screenshots

Directory `/home/efe/Documents/QRadar-Dash-work/ui58/png/after/`: 237 PNGs (26 routes x loaded, loading, error x 3 viewports, plus `config-changes-loaded-*`), named `<route>-<state>-<w>x<h>.png`. The full run and the re-run of `root`, `admin-ips-source`, `admin-waf-source`, `admin-classification` are from this review's final build. Baseline in `.../png/before/`; the discarded skeleton captures are in `.../png/inherited-before/` and `.../png/inherited-after/`.

Opened and checked:
- `.../png/after/config-changes-loaded-1280x800.png`: h1 "Configuration changes", one notice; no link, button or table.
- `.../png/after/alerts-loading-1440x900.png`: real h1 "Alerts", plain "Loading alerts..." notice; no skeleton.
- `.../png/after/admin-waf-source-loading-1280x800.png`: h1 "WAF source", plain loading notice, after the offset fix.
- `.../png/after/offense-detail-error-1024x768.png`: breadcrumb and h1 kept, the alert says what failed and what to do, "Try again" button; no overflow.
- `.../png/after/behavior-waf-source-error-1440x900.png`: h1 plus alert and retry.

The gap between the h1 and the notice is the page header's own margin, the same as on loaded pages. The other 232 PNGs were not opened one by one; the measurement JSON covers them (no overflow, stable `h1`, zero skeleton nodes).

Processes: fixture (PID 1575036), headless Chrome (PID 1575038) and `next start` were all stopped by PID; ports 3158/8158/9158 are free.
