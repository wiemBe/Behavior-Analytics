# UI-3.1 report

## 1. Files changed

New, under `frontend/src/widgets/`: `types.ts` (WidgetDef, WidgetState, layout v1), `permissions.ts` (KNOWN_PERMISSIONS), `registry.ts` (11 live + 2 blocked, next/dynamic ssr:false), `Placeholder.tsx` (text-only loading), `state.ts` (deriveStale), `layout.ts` (columnsForWidth, deriveLayout, firstFreeSpot, newWidgetId), `templates.ts` (SOC Overview v1), `DashboardView.tsx`, `Catalog.tsx`, `dashboard.css`, and tests `registry.test.ts`, `state.test.ts`, `layout.test.ts`, `DashboardView.test.tsx`.

Edited:
- `components/home/WidgetFrame.tsx`: accepts the UR.5.4 union (`state: ResourceState | WidgetState`), all 8 kinds, `WidgetEditContext` (Remove replaces the drill-down link in customize mode).
- `components/home/WidgetFrame.test.tsx`: new tests appended (every kind, KPI variant, Remove).
- `components/home/HomeDashboard.tsx`: keeps header and data-age report, renders SOC Overview through DashboardView.
- `components/home/dashboard.css`: fixed grid removed (moved to `widgets/dashboard.css`); only `.home{min-width:0}` remains. I did not delete the file (AGENTS.md section 1).
- `app/tokens.css`: `--dash-row`, `--dash-gap`, `--dash-drawer-w`, `--scrim`.
- `lib/api.ts`: `CallerInfo` and `homeReads.me()` (GET /me existed in the backend; no frontend read did).
- `app/page.tsx` needed no change (it renders HomeDashboard).
- No Phase 2 widget component was changed.

## 2. What I did

- Registry, state union, deriveStale, template, geometry, DashboardView, customize (Add, Remove, Done, session-only line, no editing below 12 columns, "Widen the window to customize"), catalog dialog (by category, reads, needs, blocked and forbidden reasons, focus in on open, Esc and Close return focus to the trigger), as specified.
- Geometry: `--dash-row` 24 px, gap 12 px; box = h x 24 + (h-1) x 12. Template heights: KPI h7 (240 px), fleet EPS and class h12 (420), top/severity/health h15 (528), recent tables h15 (528). Chosen as the smallest that scroll nothing at 1440 and 1280 with the ok fixture (audit: zero scrolling bodies, page width equals client width at all three viewports). All h are within the stored layout bound of 24.
- Below 12 columns (derived 6 and 1) rows take their content's height instead of fixed boxes (read-only view; its widgets are narrower than their boxes were sized for). This keeps the 1024 reflow identical to Phase 2.
- Widgets load asynchronously, so the Home page test now waits for the placeholders to go (see section 4).
- Placed widgets carry grid numbers as CSS custom properties (`--dg-x/y/w/h`) in a style attribute, the only inline style; every length is in CSS. Flagging it as AGENTS.md 3a says no inline styles.

### Interpretations to confirm
- deriveStale: UR.5.4 says "default of 2 h" for the cadence; the task says "2 h when the cadence is unknown". I took 2 h as the stale threshold (stricter; flags sooner). One constant to change.
- `/me` is read only when the catalog opens (not on every Home load); `effective_permissions` is used.
- Stale wiring: not added to widgets. Candidates: KPI offenses and Recent offenses (offense snapshot `captured_at`), Fleet EPS and EPS by class (`window_end`), Log source health (`last_bucket_at`).
- The 12-column grid does not compact vertically after Remove (a gap stays; Add fills it). Compaction arrives with react-grid-layout.

### Home versus the approved Phase 2 PNGs
Same order, spans, 1024 reflow (identical arrangement: KPI 2x2, fleet full width, class beside top sources, severity beside health, tables full width), same content. Differences, all from fixed boxes:
- 1440: KPI cards 240 px (Phase 2: about 210), fleet row 420 (385), middle row 528 (490), bottom row 528 (524); the page is about 130 px taller. Charts grow to fill, so the extra is in the KPI cards' bottom (about 30 to 55 px blank under shorter cards) and under short tables. 240 is needed because the 1280 KPI text wraps to about 231 px; a finer row cannot shrink it without a scroll at 1280.
- 1280 and 1024: no visible difference beyond the Customize button (1024: the "Widen the window to customize" line) in the header.

## 3. Gates (verbatim)

- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, no output (clean)
- `npx vitest run --no-cache`: `Test Files  141 passed (141)` / `Tests  2033 passed (2033)`
- `npm run build`: `✓ Compiled successfully in 6.7s`

### Break-it list (each broken, the named tests failed, restored)
1. state.ts `>` to `>=` at the stale boundary: 2 failed.
2. layout.ts row-wrap test `x + w > cols` to `cols + 1`: 2 failed.
3. layout.ts overlap check loses its second condition: 3 failed.
4. Catalog.tsx blocked check disabled: 1 failed.
5. Catalog.tsx missing-permission filter emptied: 1 failed.
6. DashboardView.tsx `editing` no longer requires 12 columns: 1 failed.
7. DashboardView.tsx focus return to the trigger removed: 2 failed.
8. DashboardView.tsx unknown-type branch disabled: 1 failed.
9. WidgetFrame.tsx no_data lead removed: 1 failed.
10. permissions.ts drops `read:geo` (drift from rbac.py): 1 failed.
11. registry.ts a source that api.ts never calls: 1 failed.

## 4. Edits to existing tests
- `app/page.test.tsx`: added a `widgetsLoaded(container)` wait (placeholders with `aria-busy` gone), made the five Home tests `async` and added `waitFor` to the import. Reason: widgets now load through `next/dynamic`. No assertion changed or removed. The route loading/error fallback test is untouched.
- `WidgetFrame.test.tsx`: appended only; existing tests unchanged.

## 5. Screenshots (/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/a16a3a57-0bb1-488e-a4d5-dadbc9e16dfd/scratchpad/ui31/png/)
Home through DashboardView (ok fixture; compare with /home/efe/Documents/QRadar-Dash-screens/phase2/ok-*):
- home-1440x900-first.png, home-1440x900-full.png, home-1280x800-first.png, home-1280x800-full.png, home-1024x768-first.png, home-1024x768-full.png
Customize and catalog:
- customize-1440x900-first.png and -full.png, customize-1280x800-first.png and -full.png: Add widget and Done in the header, the session-only line, Remove in place of each widget's link.
- catalog-1440x900.png, catalog-1280x800.png: drawer top; Log sources disabled with "Your role lacks permission read:overview." (fixture /me lacks it).
- catalog-1440x900-scrolled.png, catalog-1280x800-scrolled.png: Geography and IPS source anomalies disabled with their reasons.
- removed-1440x900-full.png (Recent offenses removed, gap left), readded-1440x900-full.png (added back at the same spot; positions verified identical).
- hint-1024x768-first.png: "Widen the window to customize".
Geometry audit (script outside the repo): no scrolling body at 1440 or 1280, also in customize mode; no page overflow at any viewport.

## 6. Open points
- The catalog's blocked entries sit in their categories (Chart, Table), so one screenshot cannot show a blocked and a forbidden entry at once without scrolling; two PNGs per viewport instead.
- Fixture /me is mine (no backend call was made); the catalog's /me path against the real backend is untested.
- Not verified: real-browser behaviour of the native dialog backdrop (headless Chrome showed it correctly).

## Round 2

- `widgets/state.ts`: `DEFAULT_CADENCE_SECONDS = 2 h`; threshold is 2 x cadence, so 4 h with an unknown cadence (UR.5.4). Tests: 3 h 59 min fresh, 4 h and 4 h 1 min stale/boundary. Break-it: default cadence set to 1 h, 1 test failed, restored.
- `widgets/Catalog.tsx`, `DashboardView.tsx`: a widget already on the dashboard is disabled with "Already on this dashboard." (comment notes UI-3.3 configuration makes duplicates meaningful). Test added; two existing assertions in my own new catalog tests (Alpha enabled) changed to match. Break-it: guard disabled, 2 tests failed, restored.
- `cell()` carries the comment accepting the custom-property exception.
- Re-captured `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/a16a3a57-0bb1-488e-a4d5-dadbc9e16dfd/scratchpad/ui31/png/catalog-1440x900.png` and `catalog-1280x800.png` (placed widgets show "Already on this dashboard.").

Gates (verbatim):
- lint: `For existing projects, migrate to the ESLint CLI:
✔ No ESLint warnings or errors`
- typecheck: `` (tsc --noEmit, clean)
- vitest: ` Test Files 141 passed (141)/ Tests 2034 passed (2034)`
- build: ` ✓ Compiled successfully in 6.2s`

## Orchestrator review (2026-10-14)

Accepted after round 2. The orchestrator looked at the Home at 1440/1280/1024 against the Phase 2
PNGs, customize mode, both catalog captures and the 1024 hint, checked that no existing assertion
was removed, and re-ran the gates: lint clean, typecheck clean, 141 files / 2,034 tests, build
compiled. Rulings: the `--dg-*` custom properties are the one accepted inline-style exception
(unitless layout numbers only); the ~130 px taller Home at 1440 is accepted; the stale threshold
with an unknown cadence is 4 h (UR.5.4: the 2 h default is the cadence). Carried to the next UI
task: "Already on this dashboard" uses the warning colour for neutral information (should be muted).
