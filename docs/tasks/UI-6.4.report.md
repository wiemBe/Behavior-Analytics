# Task UI-6.4 — cleanup: dead CSS, dead exports, literal check (Phase 6 QA)

Worktree `agent/ui64`, based on `feat/phase-ce @ 28fbb9d`. Ran as the sandboxed opencode agent.
Scratch, fixtures, capture script, PNGs and logs: `/home/efe/Documents/QRadar-Dash-work/ui64/`.

## 1. Files changed

| File | Change |
|---|---|
| `frontend/src/app/globals.css` | Removed 6 dead selectors (see §2.1), −22 lines net: `.panel-pad-lg`, the `.metric-label` and two `.metric-value` branches of grouped headers, `.kv td` + `.kv td:nth-child(odd)`, `.stack-7 > * + *` |
| `frontend/src/components/catalog/catalog.css` | Removed `.cw-table th.cw-th-sub` and `.cw-group + .cw-group` (−6 lines) |
| `frontend/src/widgets/dashboard.css` | Removed `.dg-session` and `.page-header-actions:has(.dg-session)` (−13 lines) |
| `frontend/src/app/colourLiterals.test.ts` | New: the contract test of §2.3 (no colour literal outside `tokens.css`, every stylesheet under `src`) |

No `.tsx`/`.ts` file was edited or deleted (§2.2). Nothing else touched; `git status` shows the
three stylesheets modified and the one new test.

## 2. What I did

### 2.1 Dead CSS

Method: every rule header in every `frontend/src/**/*.css` (`tokens.css` excluded) was parsed
(script `QRadar-Dash-work/ui64/scripts/css-selectors.mjs` → `selectors.json`, 881 selector blocks,
584 distinct class names); each class was then searched for as a literal in every `src/**/*.{ts,tsx}`
including tests (`css-usage.mjs` → `usage.json`). A rule was a removal candidate only when **every**
class it names is absent from all TS/TSX. Each candidate was then re-verified individually: no test
(`shellFixes`, `nowrapContract`, `stickyAndStatGrid`, `shortViewportNav`, `TableScroll`,
`DashboardMenu`, `WidgetFrame.chrome`, `KpiRow`, `alerts/[id] page`) reads any of them; no doc under
`docs/` names them; and `git log -S` confirms each name stopped being *applied* for good in the
commit cited below.

Removed (all provably unused):

| File | Selector | Evidence it is unused |
|---|---|---|
| `app/globals.css` | `.panel-pad-lg` | 0 TS/TSX/test occurrences. Last code use removed in `01bcd86` (UR.0 restyle) |
| `app/globals.css` | `.metric-label` (branch of `.card .k, .metric-label`) | 0 occurrences; the `.card .k` branch stays and renders `StatCard`/detail keys |
| `app/globals.css` | `.metric-value` (branch of `.num, .card .v, .metric-value, .measure-value, td.num` and of `.card .v, .metric-value`) | 0 occurrences; the `.card .v` branch stays |
| `app/globals.css` | `.kv td`, `.kv td:nth-child(odd)` | no element with class `kv` is ever rendered — pages use `kv-table` (`offenses/[id]/page.tsx`, `rules/[id]/page.tsx`), styled by `offense-detail.css` / `rules.css` |
| `app/globals.css` | `.stack-7 > * + *` | 0 occurrences; every page uses `stack-6` |
| `components/catalog/catalog.css` | `.cw-table th.cw-th-sub` | 0 occurrences. Added in `19512c8` (UI-3.5a), never applied |
| `components/catalog/catalog.css` | `.cw-group + .cw-group` | 0 occurrences. Same commit |
| `widgets/dashboard.css` | `.dg-session`, `.page-header-actions:has(.dg-session)` | UI-4.2 (`64ace60`) deleted the `dg-session` element from `DashboardView.tsx` ("Customizing…" lines) and left the rules behind |

Dynamic classes that the scan flags absent literally, kept because they are enumerated by a
template literal (listed per the task):

- `mag-w-0` … `mag-w-10` — `` `mag-bar ${magnitudeTone(m)} mag-w-${magnitudeStep(m)}` `` ranges 0–10
  (`offenses/page.tsx`, `offenses/[id]/page.tsx`);
- `boundary-state-small` / `-medium` / `-large` — `` `boundary-state boundary-state-${offset}` ``
  with `"none" | "small" | "medium" | "large"` (`boundary/BoundaryLoading.tsx`);
- `fm-banner.is-ok` and its siblings — `` `fm-banner is-${msg.tone}` `` with
  `"ok" | "crit" | "warn" | "info"` (WAF and IPS profile editors);
- `react-grid-item`, `react-grid-placeholder`, `react-resizable-handle`, `cssTransforms`,
  `resizing`, `react-draggable-dragging` — emitted at runtime by `react-grid-layout` /
  `react-draggable` under `.dg-edit` (verified in `node_modules`).

Search artifacts not selectors (kept, no action): `@container tw …` (container name, not a class),
`.card .v`, `.card .k` (used in TSX), `@import` headers.

### 2.2 Dead exports and files

Method: every `export` in `src/**/*.{ts,tsx}` collected (`export-scan.mjs` → `exports.json`;
1,252 symbols). For each, the defining file's own text was excluded and *every other* file searched
for the bare name on word boundaries — imports, JSX use, type use, comments, tests.

- **No file qualifies for deletion.** 541 TS files were checked against every import specifier in
  the tree (`import x from "@/y"`, relative and dynamic). Every non-test file is imported by
  something else or is a Next.js convention file (`page.tsx` / `layout.tsx` / `loading.tsx` /
  `error.tsx` / `route.ts`). Files whose only remaining importer is their own test stay by rule.
  **List of files deleted: none.**
- **Dead exports, reported and kept (204).** All are exported symbols whose name occurs nowhere
  outside their defining file, tests included (full list in the appendix below). They fall into
  three shapes: module-internal constants/types exported unnecessarily (`WIDE_MIN_PX`,
  `RefreshChoice`, `NavItem`…), response-shape interfaces in `lib/api.ts` (47), and stray helpers
  (`ConfirmLayoutDialog`, `hasQualifier`, `runBucketTime`…). Consequences:
  - Two Next-convention false positives excluded from the list:
    `ClassificationRulesPage` (`app/admin/classification-rules/page.tsx`) and `AdminLayout`
    (`app/admin/layout.tsx`) — imported by the framework.
  - Removing `export` from any of these is an edit to a live file, which the task's allowed-file
    list does not include ("ts(x) files that are dead per item 2 (deletion), or that only re-export
    a removed symbol"), so they are reported, not stripped.
  - One extra finding: in `components/ips/ipsFormat.ts` the re-export line
    `export { bucketWidth, formatPercent, formatPoints } from "@/components/waf/wafFormat"` — the
    `formatPoints` branch is dead (only `WafIncidentMetrics.tsx` imports `formatPoints`, directly
    from `./wafFormat`); `bucketWidth` and `formatPercent` are genuinely imported through the
    re-export and stay. Same live-file rule: reported, not edited.

### 2.3 Literals

- **Colour literals: zero, confirmed by sweep.** Every stylesheet except `tokens.css` was scanned
  for `#hex`, `rgb(`, `rgba(`, `hsl(`, `hsla(` and named colours (a word sitting in a declaration
  *value*, so `white-space` is not a false hit; `transparent`/`currentColor`/`inherit` allowed) —
  28 files, 0 hits, both before and after my edits. Nothing to fix, no new token needed. (A first
  pass of the sweep had one false positive per file: the `white` in the property name
  `white-space`; corrected in the script and mirrored in the test.)
- **New contract test** `src/app/colourLiterals.test.ts` (29 tests: a sweep guard plus one per
  stylesheet, 28 files): walks every `*.css` under
  `src` recursively, pins "no colour literal outside `tokens.css`" per file, and a guard test
  asserting the sweep covers > 20 files so a new directory cannot silently escape it.
  BREAK-IT runs green-to-red: appending a `.made-up { color: #123456; }` rule to `admin.css`
  failed exactly one test ("hex literal"), restored after; `git diff` on `admin.css` clean again.
  The existing `contrast.test.ts` was not weakened — its own colour sweep (globals, fonts,
  components) still passes unchanged.
- **Spacing/type-size literals (px or rem values not in `tokens.css`), per file, not converted
  (task says report only).** 195 total across 28 files:

  | File | px/rem literals |
  |---|---|
  | `app/globals.css` | 134 |
  | `widgets/dashboard.css` | 27 |
  | `components/home/kpi.css` | 4 |
  | `app/admin/admin.css`, `app/fleet/catchall/catchall.css`, `components/Evidence.css`, `components/home/tables.css`, `components/ips/ips.css` | 3 each |
  | `app/anomalies/[id]/incident.css`, `app/anomalies/anomalies.css`, `app/behavior/overview.css`, `app/behavior/sources/sources.css`, `app/rules/rules.css`, `components/ui/controls.css` | 2 each |
  | `app/coverage/coverage.css`, `app/offenses/[id]/offense-detail.css`, `app/offenses/offenses.css` | 1 each |
  | 10 files with 0 (`alerts/*.css`, `app/behavior/waf/waf.css`, `fonts.css`, `log-sources/*.css`, `searches/[id]/search-detail.css`, `boundary.css`, `catalog.css`, `charts.css`, `home/dashboard.css`) | 0 |

  They are almost all `1px` hairlines, `2px` marks and chart tick offsets; no colour among them.

### 2.4 Proof that nothing visible changed

- Harness: the T-111 capture re-used as the task allows (`capture.mjs` copied to
  `QRadar-Dash-work/ui64/`, ports 3164 / 8164 / 9164, Chrome `--user-data-dir` under the work dir,
  fixtures in `fixture/`). Six fixtures cover the twelve routes; 24 shots per pass
  (12 routes × 1440×900 and 1024×768):
  `/` (dashboard, `seed=none&write=0`), `/behavior`, `/behavior/sources/[B1]`, `/anomalies`,
  `/anomalies/a-1`, `/behavior/waf`, `/fleet`, `/log-sources`, `/alerts`, `/rules`,
  `/rules/statistics`, `/admin`. Fixture servers copied to scratch and edited there: default port
  8111 → 8164 and CORS origin 3111 → 3164. The top-bar clock (`.topbar-times time`, a live
  "Refreshed" time) is blanked by the shot script before every capture.
- Document measurements are **identical before/after for all 24 shots** (`scrollWidth/clientWidth`,
  `pageH`, in `logs/measurements-before.json` / `-after.json`).
- Pixel diff (full RGBA, `pixel-diff.mjs`), `png/before` vs `png/after`:

  | Route shot | diff px | % |
  |---|---|---|
  | admin ×2, anomalies-1440, anomaly-detail ×2, fleet-1440, log-sources ×2, rules ×2 | 0 | 0.0000 |
  | home-1440 10, home-1024 25, rules-statistics-1024 25, anomalies-1024 21, fleet-1024 36, waf-1440 3 | ≤36 px | ≤0.0046 % |
  | behavior-1024 69, behavior-1440 283, behavior-source-1024 145, behavior-source-1440 3021 | up to 3021 px | up to 0.2331 % |

  Every nonzero group is explained, and none is a styling regression:
  1. **`/behavior` pages (`behavior`, `behavior-source`): data text moved by the harness, not by
     CSS.** The `ui51` fixture pins its clock to the floor of the *real* wall clock to a
     5-minute bucket (`fixture/ui51/server.py` line 10), so captures minutes apart render
     different "Started"/"Last event" datetimes (e.g. `10-16 05:40 UTC` vs `05:45 UTC`) on both
     viewports. Control run with **the CSS edits already in place**: `after2` (11:17–11:19) vs
     `after3` (11:23) — an unchanged build — reproduce diffs of the same kind and bigger
     magnitude (`behavior-1440` 746 px vs 283 before/after; `behavior-source-1440` 2818 px vs
     3021), all confined to the same time-text cells. The two after runs sit inside one
     5-minute bucket, hence their zero diff.
  2. **The remaining ≤36 px specks (top bar `rows 13-44`, single chart dots): rasterisation
     antialiasing only.** A magnitude-gated diff (channel delta > 40) brings all of them to 0 —
     every affected pixel differs by at most 8 of 255 — while the behavior pages' gate at >40
     isolates exactly the time-text cells above. Diff masks in `/tmp` during the run showed no
     pixel outside canvas-glyph edges: one "6" glyph redrawn on a chart axis, the top-bar edge
     and an em dash.
- The layout-affecting claim is the measurements: every route rendered at exactly the same
  document and page dimensions before and after on both viewports.

## 3. Gates

From `frontend/`, `~/.local/node/bin` on PATH, after all edits:

- `npm run lint` — `✔ No ESLint warnings or errors`
- `npm run typecheck` — clean (no output, exit 0)
- `npx vitest run --no-cache` — `Test Files  247 passed (247)` / `     Tests  2886 passed (2886)` /
  ` Start at  11:15:46` / ` Duration  38.02s …`
- `npm run build` (with `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8164/api/v1`) —
  `✓ Compiled successfully in 4.3s`, 25/25 pages generated, no error

All processes stopped by PID afterwards (`next-server` 190828, headless Chrome 177450; ports 3164
and 9164 free). Nothing committed.

## 4. Open points

- The 204 dead exports of §2.2 are report-only: stripping `export` from a live file is outside
  this task's allowed-file list. If a follow-up wants them, a mechanical un-export is one
  `sed`-class change per symbol plus a full vitest run.
- `lib/api.ts` alone holds 47 exported-but-untyped-elsewhere interfaces; most mirror the backend
  response shapes 1:1 and may be deliberate zero-cost documentation — worth an orchestrator
  ruling before anyone un-exports them.
- `components/ips/ipsFormat.ts` re-exports `formatPoints` through itself for nobody; kept (live
  file), reported in §2.2.
- The `.topbar-times time` masking in the capture script blanks the fixture-pinned "Data to" time
  as well as the live "Refreshed" time — no effect on the comparison (equal treatment), noted in
  case the harness is re-used where a fixture-pinned time must survive.
- UI-6.1 (running in parallel) may make its own one-line route-CSS fixes; a later merge should
  reconcile `globals.css`/`catalog.css`/`dashboard.css` against this branch trivially (pure
  deletions, no shared lines with what UI-6.1 would add).

## Appendix — dead exports (204, report-only)

```
app/admin/ips-sources/ips.ts: isForbidden, referenceText
app/admin/outage-policy/SourceTable.tsx: NO_LONGER_IN_INVENTORY
app/admin/outage-policy/outage.ts: GraceParse, isGlobal
app/fleet/catchall/catchall.ts: EmptyKind, SlotState
app/fleet/catchall/fixtures.ts: SRC_A, SRC_B
components/behavior/AnomalyFilters.tsx: AnomalyFilterValues
components/behavior/AnomalyRows.tsx: WAF_LIST_UNIT
components/behavior/VolumeChart.tsx: AnomalyInterval, TransitionMarker
components/catalog/RecentIpsAnomalies.tsx: IPS_EMPTY
components/catalog/SourceEpsChart.tsx: SourceEpsModel
components/catalog/WafSources.tsx: WAF_SOURCE_ROWS
components/catalog/derive.ts: CATCHALL_ROWS, DetectionView, FLEET_CLASS_ROWS, LATE_TOLERANCE_SECONDS, OutageRow
components/catalog/ipsAnomalies.ts: IpsAnomaliesView, IpsAnomalyRow
components/catalog/subject.ts: BucketFacts
components/fieldmapping/FieldMappingForm.tsx: CopyOption
components/fieldmapping/fixtures.ts: FULL_MAPPING
components/home/EpsByClass.tsx: classHref
components/home/HomeDashboard.tsx: TEMPLATE_PARAM, dashboardHref
components/home/KpiTestStates.tsx: RECEIVED
components/home/RecentAnomalies.tsx: WAF_EMPTY
components/home/SeverityChart.tsx: anomaliesHref
components/home/WidgetFrame.tsx: WidgetFrameProps
components/home/chartOptions.ts: OBSERVED_NAME, prefersReducedMotion
components/home/useEChart.ts: ChartClick
components/ips/IpsSeriesChart.tsx: IpsInterval, IpsTransition, measurementMeaning
components/ips/ipsBaseline.ts: IpsBaselineLookup, IpsSeasonal
components/ips/ipsDetectors.ts: IpsDetectorSpec, IpsFigures
components/ips/ipsLabels.ts: IPS_SUBJECT_KINDS
components/ips/ipsSeries.ts: IpsMark, IpsMarkedInterval, IpsSeriesCounts, SourceSeriesKind
components/ips/ipsSummary.ts: IpsSummary
components/rules/ruleStatistics.ts: BUILDING_BLOCK_TYPE_NOTE, DEFAULT_DAYS, DEFAULT_RANK, DayChoice, PivotGroup, PivotRow, STATS_PATH, UNKNOWN_ORIGIN, UNKNOWN_ORIGIN_LABEL, UNKNOWN_TYPE_LABEL, WindowState
components/shell/AutoRefresh.tsx: RefreshChoice
components/shell/ConnectionStatus.tsx: Signal, SignalTone
components/ui/Tabs.tsx: TabSpec
components/waf/WafInvestigation.tsx: wafChartSummary
components/waf/WafRequestChart.tsx: WafInterval, WafTransition
components/waf/fixtures.ts: WAF_ENDPOINT_ID
components/waf/subjectQuery.ts: SubjectQuery
components/waf/wafRange.ts: DEFAULT_WAF_RANGE
components/waf/wafSeries.ts: WafPoint, ratioOf
components/waf/wafSourceHealth.ts: ValidationView
components/waf/wafSummary.ts: WafSummary
lib/anomalyQuery.ts: AnomalyQuery
lib/api.ts: AgingBucket, AnomalyListParams, CatchallBucketList, CatchallMeasurement, CatchallSenderParams, CatchallSource, CatchallTopSender, ClassificationRuleCreate, ClassificationRuleUpdate, CostRow, CountEntry, EpsCoverage, EpsSeries, FleetSeriesGroup, FleetSourceCounts, FleetTopSource, HomeRead, IpsAdmission, IpsAnomalyTypeName, IpsDimensionCoverage, IpsDimensionName, IpsEntityInventory, IpsEntityPressure, IpsLatestBucket, IpsMappingFieldOut, IpsPresetOut, IpsSourceBehavior, MappingValueMapOut, OutagePolicyUpsert, OutageScopeKind, PagedIpsBaselines, PagedIpsEntities, PagedIpsEntityMetrics, PagedIpsSourceMetrics, PagedIpsSources, PagedWafBaselines, QueryProvenance, QueryProvenanceEntry, RuleDependency, SearchCadenceLastRun, SilentExclusion, SilentRule, WafAdmissionOverflow, WafAnomalyTypeName, WafLatestBucket
lib/behavior.ts: BandObservation, completenessTone
lib/behaviorOverview.ts: AttentionRow, HIGH_DEVIATION_RATIO, LOW_DEVIATION_RATIO, compareAttention
lib/contributors.ts: HEADLINE_DIMENSION_ORDER
lib/dashboardApi.ts: DashboardCreate, DashboardPatch, DashboardReplace, DefaultDashboardOut, LayoutRead, PagedDashboards, ReplaceResult, asSentence
lib/fieldMapping.ts: Assignment, CatalogNote, ProfileStateInput, PropertyMatch, ValidationKind, ValidationState, ValueChange, ValueRow, mappingChanges, suggestionHint
lib/fleet.ts: CLASS_LABELS, CompletenessInfo, DEFAULT_WINDOW, ExpectedInfo, FLEET_SOURCE_PAGE_SIZE, RuleFailure, RuleLevel, concernsPattern, isRegexKind
lib/home/anomalies.ts: SeverityCount, TypeCount
lib/home/epsByClass.ts: REST_LABEL
lib/home/epsChart.ts: EpsGap, EpsStep
lib/home/healthCategories.ts: HealthCategory, HealthCategoryInputs
lib/home/qualifiers.ts: hasQualifier
lib/home/recentAnomalies.ts: RecentAnomaliesView, RecentAnomalyRow
lib/home/recentOffenses.ts: RecentOffenseRow, RecentOffensesView
lib/home/severityPerHour.ts: HourBucket
lib/home/sparkline.ts: SparklineGeometry
lib/home/topSources.ts: DeviationGap, TOP_N
lib/home/totalEps.ts: ExpectedCoverage
lib/nav.ts: NavItem
lib/searchCadence.ts: OutcomeTone, SchedulePreset, WorstCaseDelay
lib/searchRuns.ts: ResultCount, ThresholdVerdict, TrendRow, runBucketTime
lib/sourceDetail.ts: BaselineStatus, CollectionStatus
lib/sourceInventory.ts: SortDirection, SourceSort
lib/summarize.ts: IncidentSummary
widgets/Catalog.tsx: ALREADY_PLACED, unavailableReason
widgets/DashboardMenu.tsx: DashboardAccess, DashboardMenuProps, MenuAction, PERM_SHARE
widgets/DashboardSaveDialogs.tsx: ConfirmLayoutDialog
widgets/DashboardSwitcher.tsx: DashboardSwitcherProps
widgets/DashboardView.tsx: DashboardViewProps
widgets/layout.ts: MEDIUM_MIN_PX, WIDE_MIN_PX
widgets/permissions.ts: KnownPermission
widgets/state.ts: Staleness
widgets/types.ts: ConfigField, ConfigOptionList, OptionsRead
widgets/useDashboard.ts: DashboardEditorAction, DashboardEditorState, DashboardSaveState, UseDashboardOptions
```

PNGs for review (before | after), 24 each, in
`/home/efe/Documents/QRadar-Dash-work/ui64/png/before/` and `…/after/`:
`home-{1440x900,1024x768}.png`, `waf-*`, `fleet-*`, `admin-*`, `behavior-*`,
`behavior-source-*`, `anomalies-*`, `anomaly-detail-*`, `log-sources-*`, `alerts-*`, `rules-*`,
`rules-statistics-*`. Control passes: `png/after2/` (24) and `png/after3/` (behavior only).
