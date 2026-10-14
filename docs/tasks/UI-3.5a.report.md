# UI-3.5a report

## 1. Files changed
New, `frontend/src/components/catalog/`: `HealthDetection.tsx`, `HealthCollection.tsx`, `HealthEvidence.tsx`, `CatchallSenders.tsx`, `FleetClasses.tsx`, `OutagePolicy.tsx`, `WafSources.tsx`, `RecentWafAnomalies.tsx`, `derive.ts` (pure derivations), `catalog.css` (one figure style, tokens only), `fixtures.ts`, `catalog.test.tsx`, `states.test.tsx`.
Edited:
- `widgets/registry.ts`: eight live entries (+ a `SMALL` size).
- `widgets/registry.test.ts`: the exact live-type list gained the eight types (the test name too); no guard changed.
- `widgets/Catalog.tsx`, `widgets/dashboard.css`: "Already on this dashboard." gets class `info` (muted `--text-3`); blocked and permission reasons keep the warning colour.
- `widgets/DashboardView.test.tsx`: one test added.
- `components/home/RecentAnomalies.tsx`: optional `subject` prop (WAF_ENDPOINT); default behaviour unchanged. A WAF list does not read the not-judged summary and has its own empty text.
- `lib/api.ts`: `homeReads` entries (coverageSummary, ruleHealthSummary, searchCadence, newCatchallSenders, outageSummary, wafSources, activeWafAnomalies). All take the AbortSignal through `homeRead`.
No tokens added. Home template untouched.

## 2. What I did (widget, question, size at 12 columns, category)
| Type | Question | Size (w x h) | Category |
|---|---|---|---|
| health.detection | Is detection coverage holding up? Covered share of techniques, other statuses, rule verdicts | 4 x 15 | Health |
| health.collection | Are the scheduled searches on time? Cadence, last run, late by | 6 x 15 | Health |
| health.evidence | Is evidence collection keeping up? Pending and failed, in anomalies | 4 x 10 | Health |
| table.catchall_senders | Which unknown senders need review? Newest 5 of the NEW queue, 24 h count as lower bound or "unknown" | 6 x 13 | Table |
| table.fleet_classes | How does each class fare? Sources, mean EPS, silent (8 busiest) | 6 x 12 | Table |
| table.outage_policy | Which policy applies? Default mode and grace, overrides, classes that depart | 4 x 10 | Admin |
| waf.sources | Are the WAF sources collecting? Profile verdict, coverage, endpoints of ceiling (6, attention first) | 6 x 12 | Health |
| table.recent_waf_anomalies | Which WAF endpoints are anomalous now? Wraps RecentAnomalies | 6 x 15 | Table |

Honesty: no mapped technique is "not evaluated", not 0 %; a family that never ran has "Late by" as a dash; a sender no top list named is "unknown"; a source with no bucket has coverage as a dash; a class with no complete bucket has EPS as a dash and no monitored source has silence as a dash; no classes means no override count; a capped read adds the TRUNCATED qualifier, incomplete buckets the PARTIAL one; a secondary read that fails leaves its half at a dash with the reason (health.detection rules). Forbidden names the permission.

Interpretations to confirm:
- table.outage_policy reads /fleet/summary, which needs read:fleet; the task lists only admin:outage. I registered `requires: ["read:fleet", "admin:outage"]`.
- "Late by": the run is due cadence + settle after the previous one; late beyond a 60 s tolerance, shown in whole minutes. "Silent" is open NO_EVENTS incidents of the class (/fleet/summary open_incidents).
- Catch-all reads up to 200 NEW senders and sorts by first_seen_at client-side (the API orders NEW then busiest); beyond 200 it says so (TRUNCATED).
- Window texts: HealthDetection has none (it wrapped the link at 1280).

## 3. Gates (verbatim, from frontend/)
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, no errors
- `npx vitest run --no-cache`: `Test Files  143 passed (143)` / `Tests  2088 passed (2088)`
- `npm run build`: `✓ Compiled successfully in 6.5s`

### Break-it list (each broken, named test failed, restored)
1. derive: coverage share with no technique forced to a number: "not evaluated" test failed.
2. derive: capped catch-all read stops qualifying: truncated test failed.
3. derive: outage list stops filtering to departing classes: 2 failed.
4. WafSources: no-bucket check broken: dash-not-percent test failed.
5. HealthEvidence: failed row shows pending count: counts test failed.
6. RecentAnomalies: WAF empty text disabled: clean-bill test failed.
7. Catalog: neutral class dropped: new catalog test failed.
8. derive: other-statuses filter includes COVERED: failed (test added after a first run showed the guard unpinned).
9. derive: late tolerance removed: failed (test added, same reason).
10. derive: silence of a class with no monitored source as 0: failed (test added, same reason).

## 4. Edits to existing tests
`registry.test.ts`: live-type list extended (required by the new entries). Nothing else removed or weakened.

## 5. Screenshots (/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/a16a3a57-0bb1-488e-a4d5-dadbc9e16dfd/scratchpad/ui35a/png/)
- placed-1440x900-first.png, placed-1440x900-full.png: Home with all eight placed through Customize, Add widget (ok fixture).
- placed-1280x800-full.png, placed-1024x768-full.png: same at 1280 and 1024 (1024 reflows to 6 columns).
- crop-1280.png: the eight widgets at 1280.
- placed-empty-1440x900-full.png (+ -crop.png): every new widget empty: not evaluated, no families, 0 evidence, no senders, no classes, no WAF sources, no WAF anomalies.
- crop-outage-empty.png: outage policy with no classes.
- placed-error-1440x900-full.png (+ -crop.png): every new widget with its read failing (HTTP 500).
- catalog-health-1440x900.png, catalog-table-1440x900.png, catalog-admin-1440x900.png: catalog drawer at the Health, Table and Admin sections with the new entries; Already on this dashboard shown muted.
Audit (script outside the repo): no scrolling body in any widget at 1440 or 1280, no page overflow at 1440, 1280, 1024. Forbidden state has no PNG; it is covered by tests.

## 6. Open points
- At 1280 "Anomaly explanations" and "Catch-all senders" are ellipsised in Scheduled searches (full name in the title attribute).
- The grid leaves vertical holes after placement (no compaction until the drag grid); widgets with fewer rows than their box (WAF sources with 3 sources) are partly empty.
- Empty/error crops were cut before the last, cosmetic removal of the Detection window text.
- No real-backend run; fixture API only.

# Round 2

## 1. Files changed
- `frontend/src/components/catalog/HealthCollection.tsx`: family cell gets class `cw-wrap` (title kept).
- `frontend/src/components/catalog/catalog.css`: `.tw-table td.tw-name.cw-wrap` allows wrapping (`white-space: normal`, `overflow-wrap: anywhere`).
- `frontend/src/components/catalog/HealthDetection.tsx`: headline copy.
- `frontend/src/components/catalog/OutagePolicy.tsx`: unknown default mode renders the dash with its reason.
- `frontend/src/components/catalog/catalog.test.tsx`: headline assertion updated; two tests added.
- `docs/tasks/UI-3.5a.report.md`: this section.

## 2. What I did
1. Scheduled searches: the family name wraps instead of ellipsising. At 1280 "Anomaly explanations" and "Catch-all senders" show in full on two lines; at 1440 and 1024 on one. Title attribute kept. Test pins `cw-wrap` and the title; the rest is in the PNGs.
2. Detection health: "31.8 %" is the figure, "64 of 201 techniques covered" the line beside it. The "not evaluated" state is unchanged.
3. Outage policy: the code did produce a bare "by default" when `outage_default` was missing or unknown (the dash then "by default"). The round-1 empty PNG was not that case (the fixture returns ALERT; it showed "Alert by default, silence grace 1 bucket (5 min)"), and the final one is the same. The code is fixed anyway: unknown or missing mode renders the Unmeasured dash plus "The default outage mode was not returned", with no "by default". Test added.
4. Detection title row: one line at 1280 (crop-eight-1280.png, placed-1280x800-full.png); HealthDetection has no window text and the Coverage link stays on the title row.
5. Fresh PNGs of the final code, below.

## 3. Gates (from frontend/)
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, no errors
- `npx vitest run --no-cache`: `Test Files  143 passed (143)` / `Tests  2090 passed (2090)`
- `npm run build`: `✓ Compiled successfully in 6.0s`

Break-its (each broken, named test failed, restored; catalog suite back to 54 passed):
1. `cw-wrap` class removed: "lets the family name wrap instead of ellipsising it" failed.
2. Headline reverted to "of N techniques covered (M)": "leads with the covered share and its basis" failed.
3. Outage mode check forced true: "shows the unmeasured dash with its reason, never a bare 'by default'" failed.

## 4. Screenshots (/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/27db6cd6-0ae7-4ff3-8508-d61ff41a6712/scratchpad/ui35a-r2/png/)
- placed-1440x900-first.png: first screen, KPI row and Fleet EPS.
- placed-1440x900-full.png, placed-1280x800-full.png, placed-1024x768-full.png: Home with all eight placed; 1024 reflows to 6 columns; no page overflow (scrollWidth equals clientWidth at all three).
- crop-1280.png: whole grid at 1280; crop-eight-1280.png: the lower widgets, the eight included, at 1280 (family names wrapped, Detection title on one line).
- placed-empty-1440x900-full.png and -crop.png: every widget empty (outage shows "Alert by default").
- placed-error-1440x900-full.png and -crop.png: every widget failing with HTTP 500.
- catalog-health-1440x900.png, catalog-table-1440x900.png, catalog-admin-1440x900.png: catalog drawer. The scroll script did not move the list, so the Health, Table and Admin shots show the drawer scrolled to different starting points; the new entries are visible across them.

## 5. Open points
- Same as round 1: vertical holes in the grid (no compaction until the drag grid); WAF sources and Evidence collection are shorter than their boxes; fixture API only.
- The "Add" catalog PNGs are the same drawer view of round 1; the catalog code did not change.
- Processes on ports 3135, 8135, 9135 were started by me and stopped by PID; scripts and Chrome profile are in the scratch directory, outside the repo.
