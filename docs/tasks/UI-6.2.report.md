# UI-6.2 — accessibility pass report

Status: complete (2026-10-16).

## Scope and method

- Read `AGENTS.md`, `README.md`, `docs/PRODUCT-UX-GUIDELINES.md` §9–11,
  `docs/UI-REDESIGN-PLAN.md` UR.2.8 and UR.7 Phase 6, `docs/PLANNER.md` D-11,
  `docs/UI-WORKSTREAM-COMPLETION.md` §4, and `frontend/src/app/tokens.css` before making a
  UI change.
- D-11 accepted exception: `--text-3: #73757B` and `--border-strong: #35363A` are kept exactly as
  chosen by the user. Their below-AA pairs are not defects in this task.
- Repeatable checks: scripted Chrome keyboard walk over every surface named by UI-6.2;
  DOM accessibility inventory for names, roles, labelled/focusable scroll regions, table names,
  chart text alternatives, and alert/status live regions; live bounding-box measurement for
  targets; stylesheet and computed-style motion audit; existing UR.2.8 contrast test.

## Checks and findings

- Contrast baseline: `npx vitest run --no-cache src/lib/contrast.test.ts` passed all 43 tests in
  one file. No token pair below its asserted AA threshold was found. The test pins, rather than
  passes, the two D-11 exceptions named above.
- A source-wide table-name check found 26 production tables without a caption or `aria-label`.
  Each is now named at its owner. A new regression scans every production TSX source so a later
  unnamed table identifies its file and line.
- `TopSearch` exposed `aria-controls` while its result list was unmounted. It now publishes the
  relationship only while the list is present.
- A customize-mode Arrange trigger likewise named an unmounted panel. Its `aria-controls` now
  exists only while that panel is open.
- The Resolve confirmation, WAF/IPS review confirmations, and custom-rule delete confirmation
  did not all implement the dialog keyboard contract. Escape now closes them and focus returns to
  the exact opener; the destructive custom-rule confirmation initially focuses the safe Keep
  action.
- CSS transitions and chart animation were already covered by the global reduced-motion rule and
  per-chart options except `ResultTrend`. That chart now disables ECharts animation when
  `prefers-reduced-motion: reduce` is active.
- The scripted Chrome pass exercised 25 shell focus stops, 19 anomaly-list stops, and 19 outage
  editor stops. Every focused control measured a visible `2px solid rgb(232, 232, 232)` outline
  with a `2px` offset; Shift+Tab reversed the sequence without a trap.
- Dashboard switcher Escape returned to Dashboards. Both New dashboard dialogs returned to Manage.
  Add-widget Escape returned to Add widget; Configure Escape returned to the exact Configure
  opener. Keyboard Move changed Total EPS from x=0 to x=1 and keyboard Resize changed it from
  w=3 to w=4. Done saved and left customize mode; a second keyboard change followed by Discard
  removed the dirty state. At 1024 px the existing responsive contract leaves customize editing
  and exposes Save/Discard; that honest narrow fallback is what its required capture records.
- Investigation ArrowRight moved selection and focus from Evidence to Lifecycle. Resolve focused
  its terminal action, Escape closed it and returned to Resolve. WAF and IPS review regions took
  focus, then Escape returned to Review and save. Custom-rule deletion focused the safe Keep
  action, then Escape returned to the exact labelled Delete trigger.
- Final AX-tree/DOM inventory across Dashboard, anomalies, investigation, alert detail, WAF/IPS
  editors, outage policy and classification rules found no unnamed interactive elements, tables,
  or exposed images. Every `TableScroll` found was labelled and `tabIndex=0`; every table was named.
  Charts use a named `role=img` wrapper; ECharts' internal canvas is ignored by the accessibility
  tree. The encountered informational outcomes use `role=status`; action failures are pinned by
  existing/new component tests with `role=alert`.
- The first live pass found two inactive investigation tabs pointing at lazily unmounted panels.
  The active tab alone now exposes `aria-controls`; the final pass has no dangling IDREF.
- Target geometry inventory (raw live bounding boxes) found under-24 px targets. Inline table and
  chart-legend links were 15–21 px high and retain WCAG's inline-text exception. Non-inline items
  left for their layout owners are: anomaly technical-detail disclosures 47.4×21 px; anomaly
  filter checkbox 13×13 px inside a 90.8×19 px label; WAF/IPS mapping radios 13×13 px inside
  19.5 px-high labels; classification toggles 13×13 px inside 19.5 px-high labels; and the Next
  pagination link 28.7×19.5 px. These require layout/shared-control changes, so UI-6.2 reports but
  does not change them. The T-098-owned widget Expand control was not changed here; its current
  live instances measured 60.4×24 px.
- With Chrome emulating `prefers-reduced-motion: reduce`, no computed animation or transition
  duration exceeded the global 0.01 ms override. Canvas animation is covered by the existing chart
  tests plus the new `ResultTrend` regression.

## Fixes and break-it proofs

- `TopSearch`: added a role/name regression for the transient `aria-controls` relationship. With
  the relationship deliberately made unconditional, the test failed because a closed search
  retained `aria-controls="top-search-listbox"`; the fix was restored.
- `AlertActions`: added a keyboard regression which opens Resolve, presses Escape, verifies the
  group closes, and verifies focus returns to Resolve. With its Escape condition deliberately
  made unreachable, the test failed; the fix was restored.
- WAF and IPS `ProfileEditor`: added keyboard regressions which edit a valid field, open Review,
  press Escape, and verify both close and focus return. With each Escape condition deliberately
  made unreachable, its test failed; both fixes were restored.
- `RulesManager`: added a keyboard regression which opens Delete, verifies initial focus is on
  Keep, presses Escape, and verifies focus returns to Delete. Its first run caught the trigger ref
  mistakenly attached to Edit (`expected Delete, received Edit`); the owner was corrected. With
  Escape deliberately disabled, the test failed; the fix was restored.
- Table names: the new source-wide regression passed after all owners were labelled. Removing the
  Evidence table name deliberately made it fail at `src/components/Evidence.tsx:126`; the name was
  restored.
- `ResultTrend`: added a mocked-browser regression that asserts the exact reduce media query and
  ECharts `animation: false`. Deliberately forcing animation on made the test fail (`expected
  false, received true`); the fix was restored.
- Customize Arrange: added a role/name regression which requires no `aria-controls` while closed
  and verifies the relationship resolves after opening. Making the relationship unconditional
  failed the test with `Received: aria-controls="_r_0_"`; the fix was restored.
- Lazy tabs: added a role/name regression which requires only the selected tab to point at the
  one mounted panel and rechecks after selection changes. Restoring unconditional relationships
  made it fail on Lifecycle's dangling `_r_0_-panel-lifecycle`; the fix was restored.
- Restored focused suite: `Test Files  7 passed (7)`; `Tests  7 passed (7)`.

## Screenshots opened

- `/home/efe/Documents/QRadar-Dash-work/ui62/png/dialog-focus-1440x900.png` — opened at original
  resolution. The native modal dims the page, fits without horizontal overflow, and Cancel has a
  clear high-contrast focus outline.
- `/home/efe/Documents/QRadar-Dash-work/ui62/png/dialog-focus-1024x768.png` — opened at original
  resolution. The modal and both actions fit; focused Cancel is fully visible and the page has no
  horizontal overflow.
- `/home/efe/Documents/QRadar-Dash-work/ui62/png/customize-focus-1440x900.png` — opened at original
  resolution. Customize mode, its arrange panel, and the focused Left action are legible without
  obscuring the widget title or adjacent panels.
- `/home/efe/Documents/QRadar-Dash-work/ui62/png/customize-focus-1024x768.png` — opened at original
  resolution. The responsive fallback exits the edit grid while retaining unsaved state; Save and
  focused Discard remain visible in the first screen, with no horizontal overflow.

## Files changed

- `docs/tasks/UI-6.2.report.md` — repeatable methods, break-it evidence, gates, screenshots and findings.
- `frontend/src/app/admin/ips-sources/IpsProfileEditor.tsx` — Escape close and opener focus restoration.
- `frontend/src/app/admin/ips-sources/IpsProfileEditor.accessibility.test.tsx` — IPS confirmation keyboard regression.
- `frontend/src/app/admin/waf-sources/ProfileEditor.tsx` — Escape close and opener focus restoration.
- `frontend/src/app/admin/waf-sources/ProfileEditor.accessibility.test.tsx` — WAF confirmation keyboard regression.
- `frontend/src/app/alerts/[id]/page.tsx` — notification-history table name.
- `frontend/src/app/alerts/page.tsx` — alerts table name.
- `frontend/src/app/coverage/page.tsx` — technique, rule and data-source table names.
- `frontend/src/app/log-sources/page.tsx` — log-sources table name.
- `frontend/src/app/offenses/[id]/page.tsx` — offense attributes/history table names.
- `frontend/src/app/offenses/page.tsx` — offenses table name.
- `frontend/src/app/rules/[id]/page.tsx` — rule metadata/health table names.
- `frontend/src/app/rules/page.tsx` — rules table name.
- `frontend/src/app/searches/[id]/page.tsx` — query-version table name.
- `frontend/src/app/searches/page.tsx` — scheduled-searches table name.
- `frontend/src/components/AlertActions.tsx` — Resolve confirmation Escape handling.
- `frontend/src/components/AlertActions.accessibility.test.tsx` — Resolve focus-entry/return regression.
- `frontend/src/components/Evidence.tsx` — detector evidence table name.
- `frontend/src/components/ResultTrend.tsx` — reduced-motion ECharts option.
- `frontend/src/components/ResultTrend.accessibility.test.tsx` — reduced-motion chart regression.
- `frontend/src/components/catalog/CatchallSenders.tsx` — catch-all senders table name.
- `frontend/src/components/catalog/FleetClasses.tsx` — source classes table name.
- `frontend/src/components/catalog/OutagePolicy.tsx` — outage-policy table name.
- `frontend/src/components/catalog/RecentIpsAnomalies.tsx` — recent IPS anomalies table name.
- `frontend/src/components/catalog/WafSources.tsx` — WAF sources table name.
- `frontend/src/components/fleet/RulesManager.tsx` — safe initial focus, Escape and Delete focus restoration.
- `frontend/src/components/fleet/RulesManager.accessibility.test.tsx` — deletion confirmation keyboard regression.
- `frontend/src/components/home/LogSourceHealth.tsx` — source-health table name.
- `frontend/src/components/home/RecentAnomalies.tsx` — Phase A/WAF anomaly table names.
- `frontend/src/components/home/RecentOffenses.tsx` — recent offenses table name.
- `frontend/src/components/home/TopSources.tsx` — top-source table name.
- `frontend/src/components/shell/TopSearch.tsx` — no ARIA relationship to an unmounted result list.
- `frontend/src/components/shell/TopSearch.accessibility.test.tsx` — search relationship regression.
- `frontend/src/components/ui/TableNames.test.ts` — source-wide production table-name regression.
- `frontend/src/components/ui/Tabs.tsx` — no ARIA relationship to lazily unmounted panels.
- `frontend/src/components/ui/Tabs.accessibility.test.tsx` — lazy-panel relationship regression.
- `frontend/src/widgets/Grid.tsx` — no ARIA relationship to a closed Arrange panel.
- `frontend/src/widgets/Grid.accessibility.test.tsx` — Arrange relationship regression.

## What I did

Ran a scoped accessibility QA pass rather than claiming WCAG conformance. The repeatable harness
is `/home/efe/Documents/QRadar-Dash-work/ui62/browser_final.mjs`; its mode-switching deterministic
fixture is `/home/efe/Documents/QRadar-Dash-work/ui62/fixture_proxy.py`. Evidence is in
`/home/efe/Documents/QRadar-Dash-work/ui62/logs/browser-final.json`, and exact process start/stop
records are in `logs/processes.txt`. The final successful run started fixture PIDs 9/10/11, Next
PID 19, proxy PID 12 and Chrome PID 42, then stopped each exact PID; no broad process command was
used. A final socket check confirmed ports 3162, 8162 and 9162 were all closed.

## Gates

- `npm ci` — passed: `added 471 packages, and audited 472 packages in 5s`; npm reported 159
  packages seeking funding and 8 dependency advisories (2 moderate, 6 high). No audit fix was
  run. The first attempt to tee its output one directory above the required scratch directory was
  refused read-only; the command itself completed successfully and no artifact was written there.
- `npx vitest run --no-cache src/lib/contrast.test.ts` (baseline check, not the final full-suite
  gate) — `Test Files  1 passed (1)`; `Tests  43 passed (43)`.
- `git diff --check` (working-copy hygiene check) — passed with no output.
- Initial restored focused accessibility suite (seven new test files) — `Test Files  7 passed (7)`;
  `Tests  7 passed (7)`.
- `npx vitest run --no-cache src/widgets/Grid.accessibility.test.tsx` — `Test Files  1 passed
  (1)`; `Tests  1 passed (1)`.
- `npx vitest run --no-cache src/components/ui/Tabs.accessibility.test.tsx` — `Test Files  1
  passed (1)`; `Tests  1 passed (1)`.
- `npm run lint` — `✔ No ESLint warnings or errors` (Next also printed its upstream `next lint`
  deprecation notice).
- `npm run typecheck` — passed; `tsc --noEmit` produced no diagnostics.
- `npx vitest run --no-cache` — `Test Files  246 passed (246)`; `Tests  2857 passed (2857)`.
- `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.2:8162/api/v1 npm run build` — `✓ Compiled
  successfully in 3.7s`; `✓ Generating static pages (25/25)`;
  route manifest completed for all production routes.
- Final scripted production-browser pass — completed with no harness error; all nine audited page
  states had 0 unnamed interactive elements, 0 unnamed tables, 0 unnamed exposed images and 0
  dangling ARIA ID references. Computed reduced-motion exceptions: 0. All six child processes
  from the final run were stopped by their recorded exact PID.

## Open points

- The non-inline under-24 px targets listed above remain for the orchestrator because correcting
  them changes layout or shared-control geometry, which UI-6.2 explicitly excludes.
- At 1024 px, the established responsive behavior cannot keep the 12-column edit grid active; its
  capture therefore shows the dirty-state Save/Discard fallback rather than movable widgets.
- The D-11 contrast choices remain accepted exceptions and were not changed or filed as defects.
- `npm ci` reported 8 existing dependency advisories (2 moderate, 6 high); no dependency mutation
  was authorized for this accessibility task.
