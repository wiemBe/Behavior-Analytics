# UI-6.1 report — viewport matrix

558 captures, 32 routes, 186 route-states, three viewports. 540 pass. 18 fail. Document `scrollWidth` equals `clientWidth` on every capture (0 page-level overflow). No stylesheet was changed.

## Files changed

- `docs/tasks/UI-6.1.report.md` — this report.

No route stylesheet, shared CSS, component, or test was edited.

Scripts, the merged fixture, the 558 full-page PNGs, and `matrix.json` are under `/home/efe/Documents/QRadar-Dash-work/ui61/`.

## Route list

32 `frontend/src/app/**/page.tsx` files:

| Route | Source |
| --- | --- |
| `/` | `frontend/src/app/page.tsx` |
| `/admin` | `frontend/src/app/admin/page.tsx` |
| `/admin/classification-rules` | `frontend/src/app/admin/classification-rules/page.tsx` |
| `/admin/ips-sources` | `frontend/src/app/admin/ips-sources/page.tsx` |
| `/admin/ips-sources/[logSourceId]` | `frontend/src/app/admin/ips-sources/[logSourceId]/page.tsx` |
| `/admin/outage-policy` | `frontend/src/app/admin/outage-policy/page.tsx` |
| `/admin/search-cadence` | `frontend/src/app/admin/search-cadence/page.tsx` |
| `/admin/waf-sources` | `frontend/src/app/admin/waf-sources/page.tsx` |
| `/admin/waf-sources/[logSourceId]` | `frontend/src/app/admin/waf-sources/[logSourceId]/page.tsx` |
| `/alerts` | `frontend/src/app/alerts/page.tsx` |
| `/alerts/[id]` | `frontend/src/app/alerts/[id]/page.tsx` |
| `/anomalies` | `frontend/src/app/anomalies/page.tsx` |
| `/anomalies/[id]` | `frontend/src/app/anomalies/[id]/page.tsx` |
| `/behavior` | `frontend/src/app/behavior/page.tsx` |
| `/behavior/sources` | `frontend/src/app/behavior/sources/page.tsx` |
| `/behavior/sources/[id]` | `frontend/src/app/behavior/sources/[id]/page.tsx` |
| `/behavior/waf` | `frontend/src/app/behavior/waf/page.tsx` |
| `/behavior/waf/sources/[id]` | `frontend/src/app/behavior/waf/sources/[id]/page.tsx` |
| `/config-changes` | `frontend/src/app/config-changes/page.tsx` |
| `/coverage` | `frontend/src/app/coverage/page.tsx` |
| `/fleet` | `frontend/src/app/fleet/page.tsx` |
| `/fleet/catchall` | `frontend/src/app/fleet/catchall/page.tsx` |
| `/fleet/sources` | `frontend/src/app/fleet/sources/page.tsx` |
| `/log-sources` | `frontend/src/app/log-sources/page.tsx` |
| `/log-sources/[id]` | `frontend/src/app/log-sources/[id]/page.tsx` |
| `/offenses` | `frontend/src/app/offenses/page.tsx` |
| `/offenses/[id]` | `frontend/src/app/offenses/[id]/page.tsx` |
| `/rules` | `frontend/src/app/rules/page.tsx` |
| `/rules/[id]` | `frontend/src/app/rules/[id]/page.tsx` |
| `/rules/statistics` | `frontend/src/app/rules/statistics/page.tsx` |
| `/searches` | `frontend/src/app/searches/page.tsx` |
| `/searches/[id]` | `frontend/src/app/searches/[id]/page.tsx` |

## What you did

Production `next start` on 127.0.0.1:3161 served the existing build. `logs/build.log` line 22 is `✓ Compiled successfully in 10.7s` (exit 0). The build inlines `http://127.0.0.1:8161/api/v1`. `INTERNAL_API_BASE_URL` was the same URL for the server process.

Chrome was HeadlessChrome 155.0.8059.79 on CDP port 9161 (`/json/version`). The SUID sandbox helper is not mode 4755 inside this kernel sandbox, so Chrome was started with `--no-sandbox --disable-dev-shm-usage`. User-data-dir was `/home/efe/Documents/QRadar-Dash-work/ui61/chrome-profile`. Screenshots used `captureBeyondViewport` and device scale 1. `logs/capture.log` ends `done 558 failed 18 overflow-or-measure 18`.

`matrix.json` stores `doc` as a two-element array `[scrollWidth, clientWidth]`. Recounted from that file: 558 rows, 558 with `doc[0] === doc[1]`, 0 unequal, 0 missing. `pass` is false on 18 rows. Fail reasons are `inline-style 11` (12 rows) and `nowrap 1` (6 rows). `stateReached` is true on all 558. `stillLoading` is true on the 90 rows whose state is `loading` (the shot is taken while the loading sentence is on screen) and false on the other 468. Elements past the viewport: 0. Elements past the viewport inside `overflow-x: hidden` or `clip`: 0. Sibling text overlaps: 0. Capture errors: 0. Computed `overflow-x` on `html`, `body`, `.layout`, `main.main`, `.page`, and `.shell-head` is `visible` on every row. The log's `overflow-or-measure 18` counts rows whose `overflowPass` is false. In `capture.mjs` that flag is false when any measurement reason is set, so those 18 are the inline-style and nowrap rows above. They are not 18 pages wider than the viewport.

The fixture on 8161 merges the UI-5.1, UI-5.2, UI-5.3, UI-5.7, and UI-5.8 payloads. `GET /api/v1/dashboards` returns an empty list, so Home renders the SOC Overview template. `/api/v1/offenses/analytics` is served from the UI-5.8 body, because the UI-5.5 handler claims `/offenses/*` and was answering analytics with 404.

Next.js injects `<next-route-announcer style="position: absolute">` on settled pages. That is framework markup. It is counted as a library style and is not a fail.

### Summary

A cell is `pass` when `matrix.json` has `pass: true`. A fail cell is the `failReasons` string. Page overflow is a separate fact: every cell below has `scrollWidth === clientWidth`.

| Route | State | 1440×900 | 1280×800 | 1024×768 |
| --- | --- | --- | --- | --- |
| `/` | normal | inline-style 11 | inline-style 11 | inline-style 11 |
| `/` | dash-error | inline-style 11 | inline-style 11 | inline-style 11 |
| `/` | offenses-not-collected | inline-style 11 | inline-style 11 | inline-style 11 |
| `/` | eps-not-collected | inline-style 11 | inline-style 11 | inline-style 11 |
| `/` | loading | pass | pass | pass |
| `/admin` | normal | pass | pass | pass |
| `/admin/classification-rules` | normal | pass | pass | pass |
| `/admin/classification-rules` | empty | pass | pass | pass |
| `/admin/classification-rules` | error | pass | pass | pass |
| `/admin/classification-rules` | 403 | pass | pass | pass |
| `/admin/classification-rules` | 401 | pass | pass | pass |
| `/admin/classification-rules` | loading | pass | pass | pass |
| `/admin/ips-sources` | normal | pass | pass | pass |
| `/admin/ips-sources` | empty | pass | pass | pass |
| `/admin/ips-sources` | error | pass | pass | pass |
| `/admin/ips-sources` | 403 | pass | pass | pass |
| `/admin/ips-sources` | 401 | pass | pass | pass |
| `/admin/ips-sources` | loading | pass | pass | pass |
| `/admin/ips-sources/[logSourceId]` | valid | pass | pass | pass |
| `/admin/ips-sources/[logSourceId]` | failed | pass | pass | pass |
| `/admin/ips-sources/[logSourceId]` | unavailable | pass | pass | pass |
| `/admin/ips-sources/[logSourceId]` | missing | pass | pass | pass |
| `/admin/ips-sources/[logSourceId]` | error | pass | pass | pass |
| `/admin/ips-sources/[logSourceId]` | loading | pass | pass | pass |
| `/admin/outage-policy` | normal | nowrap 1 | nowrap 1 | nowrap 1 |
| `/admin/outage-policy` | empty | pass | pass | pass |
| `/admin/outage-policy` | error | pass | pass | pass |
| `/admin/outage-policy` | 403 | pass | pass | pass |
| `/admin/outage-policy` | 401 | pass | pass | pass |
| `/admin/outage-policy` | summary-403 | nowrap 1 | nowrap 1 | nowrap 1 |
| `/admin/outage-policy` | loading | pass | pass | pass |
| `/admin/search-cadence` | normal | pass | pass | pass |
| `/admin/search-cadence` | empty | pass | pass | pass |
| `/admin/search-cadence` | error | pass | pass | pass |
| `/admin/search-cadence` | 403 | pass | pass | pass |
| `/admin/search-cadence` | 401 | pass | pass | pass |
| `/admin/search-cadence` | loading | pass | pass | pass |
| `/admin/waf-sources` | normal | pass | pass | pass |
| `/admin/waf-sources` | empty | pass | pass | pass |
| `/admin/waf-sources` | error | pass | pass | pass |
| `/admin/waf-sources` | 403 | pass | pass | pass |
| `/admin/waf-sources` | 401 | pass | pass | pass |
| `/admin/waf-sources` | loading | pass | pass | pass |
| `/admin/waf-sources/[logSourceId]` | valid | pass | pass | pass |
| `/admin/waf-sources/[logSourceId]` | failed | pass | pass | pass |
| `/admin/waf-sources/[logSourceId]` | unavailable | pass | pass | pass |
| `/admin/waf-sources/[logSourceId]` | missing | pass | pass | pass |
| `/admin/waf-sources/[logSourceId]` | error | pass | pass | pass |
| `/admin/waf-sources/[logSourceId]` | loading | pass | pass | pass |
| `/alerts` | normal | pass | pass | pass |
| `/alerts` | empty | pass | pass | pass |
| `/alerts` | error | pass | pass | pass |
| `/alerts` | loading | pass | pass | pass |
| `/alerts/[id]` | normal | pass | pass | pass |
| `/alerts/[id]` | empty-evidence | pass | pass | pass |
| `/alerts/[id]` | missing | pass | pass | pass |
| `/alerts/[id]` | loading | pass | pass | pass |
| `/anomalies` | normal | pass | pass | pass |
| `/anomalies` | empty | pass | pass | pass |
| `/anomalies` | error | pass | pass | pass |
| `/anomalies` | 403 | pass | pass | pass |
| `/anomalies` | 401 | pass | pass | pass |
| `/anomalies` | loading | pass | pass | pass |
| `/anomalies/[id]` | phase-a | pass | pass | pass |
| `/anomalies/[id]` | evidence-failed | pass | pass | pass |
| `/anomalies/[id]` | truncated | pass | pass | pass |
| `/anomalies/[id]` | unavailable | pass | pass | pass |
| `/anomalies/[id]` | empty-dims | pass | pass | pass |
| `/anomalies/[id]` | no-events | pass | pass | pass |
| `/anomalies/[id]` | resolved | pass | pass | pass |
| `/anomalies/[id]` | waf | pass | pass | pass |
| `/anomalies/[id]` | waf-block | pass | pass | pass |
| `/anomalies/[id]` | waf-new-endpoint | pass | pass | pass |
| `/anomalies/[id]` | ips-port | pass | pass | pass |
| `/anomalies/[id]` | ips-signature | pass | pass | pass |
| `/anomalies/[id]` | ips-destination | pass | pass | pass |
| `/anomalies/[id]` | ips-new-port | pass | pass | pass |
| `/anomalies/[id]` | ips-concentration | pass | pass | pass |
| `/anomalies/[id]` | ips-fanout | pass | pass | pass |
| `/anomalies/[id]` | ips-block | pass | pass | pass |
| `/anomalies/[id]` | ips-failed | pass | pass | pass |
| `/anomalies/[id]` | ips-empty | pass | pass | pass |
| `/anomalies/[id]` | ips-insufficient | pass | pass | pass |
| `/anomalies/[id]` | ips-partial | pass | pass | pass |
| `/anomalies/[id]` | ips-truncated | pass | pass | pass |
| `/anomalies/[id]` | ips-lower-bound | pass | pass | pass |
| `/anomalies/[id]` | error | pass | pass | pass |
| `/anomalies/[id]` | missing | pass | pass | pass |
| `/anomalies/[id]` | 403 | pass | pass | pass |
| `/anomalies/[id]` | 401 | pass | pass | pass |
| `/anomalies/[id]` | loading | pass | pass | pass |
| `/behavior` | normal | pass | pass | pass |
| `/behavior` | error | pass | pass | pass |
| `/behavior` | 403 | pass | pass | pass |
| `/behavior` | 401 | pass | pass | pass |
| `/behavior` | loading | pass | pass | pass |
| `/behavior/sources` | normal | pass | pass | pass |
| `/behavior/sources` | empty | pass | pass | pass |
| `/behavior/sources` | error | pass | pass | pass |
| `/behavior/sources` | 403 | pass | pass | pass |
| `/behavior/sources` | 401 | pass | pass | pass |
| `/behavior/sources` | loading | pass | pass | pass |
| `/behavior/sources/[id]` | partial | pass | pass | pass |
| `/behavior/sources/[id]` | insufficient | pass | pass | pass |
| `/behavior/sources/[id]` | metrics-error | pass | pass | pass |
| `/behavior/sources/[id]` | missing | pass | pass | pass |
| `/behavior/sources/[id]` | 403 | pass | pass | pass |
| `/behavior/sources/[id]` | 401 | pass | pass | pass |
| `/behavior/sources/[id]` | loading | pass | pass | pass |
| `/behavior/waf` | normal | pass | pass | pass |
| `/behavior/waf` | empty | pass | pass | pass |
| `/behavior/waf` | error | pass | pass | pass |
| `/behavior/waf` | 403 | pass | pass | pass |
| `/behavior/waf` | 401 | pass | pass | pass |
| `/behavior/waf` | loading | pass | pass | pass |
| `/behavior/waf/sources/[id]` | partial | pass | pass | pass |
| `/behavior/waf/sources/[id]` | empty-endpoints | pass | pass | pass |
| `/behavior/waf/sources/[id]` | missing | pass | pass | pass |
| `/behavior/waf/sources/[id]` | error | pass | pass | pass |
| `/behavior/waf/sources/[id]` | 403 | pass | pass | pass |
| `/behavior/waf/sources/[id]` | 401 | pass | pass | pass |
| `/behavior/waf/sources/[id]` | loading | pass | pass | pass |
| `/config-changes` | not-collected | pass | pass | pass |
| `/coverage` | normal | pass | pass | pass |
| `/coverage` | empty | pass | pass | pass |
| `/coverage` | error | pass | pass | pass |
| `/coverage` | loading | pass | pass | pass |
| `/fleet` | normal | pass | pass | pass |
| `/fleet` | error | pass | pass | pass |
| `/fleet` | 403 | pass | pass | pass |
| `/fleet` | 401 | pass | pass | pass |
| `/fleet` | loading | pass | pass | pass |
| `/fleet/catchall` | normal | pass | pass | pass |
| `/fleet/catchall` | empty | pass | pass | pass |
| `/fleet/catchall` | error | pass | pass | pass |
| `/fleet/catchall` | 403 | pass | pass | pass |
| `/fleet/catchall` | 401 | pass | pass | pass |
| `/fleet/catchall` | loading | pass | pass | pass |
| `/fleet/sources` | normal | pass | pass | pass |
| `/fleet/sources` | empty | pass | pass | pass |
| `/fleet/sources` | error | pass | pass | pass |
| `/fleet/sources` | 403 | pass | pass | pass |
| `/fleet/sources` | 401 | pass | pass | pass |
| `/fleet/sources` | loading | pass | pass | pass |
| `/log-sources` | normal | pass | pass | pass |
| `/log-sources` | empty | pass | pass | pass |
| `/log-sources` | error | pass | pass | pass |
| `/log-sources` | loading | pass | pass | pass |
| `/log-sources/[id]` | normal | pass | pass | pass |
| `/log-sources/[id]` | sparse | pass | pass | pass |
| `/log-sources/[id]` | missing | pass | pass | pass |
| `/log-sources/[id]` | loading | pass | pass | pass |
| `/offenses` | normal | pass | pass | pass |
| `/offenses` | empty | pass | pass | pass |
| `/offenses` | error | pass | pass | pass |
| `/offenses` | loading | pass | pass | pass |
| `/offenses/[id]` | normal | pass | pass | pass |
| `/offenses/[id]` | nulls | pass | pass | pass |
| `/offenses/[id]` | history-failed | pass | pass | pass |
| `/offenses/[id]` | missing | pass | pass | pass |
| `/offenses/[id]` | invalid | pass | pass | pass |
| `/offenses/[id]` | error | pass | pass | pass |
| `/offenses/[id]` | loading | pass | pass | pass |
| `/rules` | normal | pass | pass | pass |
| `/rules` | empty | pass | pass | pass |
| `/rules` | error | pass | pass | pass |
| `/rules` | loading | pass | pass | pass |
| `/rules/[id]` | healthy | pass | pass | pass |
| `/rules/[id]` | insufficient | pass | pass | pass |
| `/rules/[id]` | history-failed | pass | pass | pass |
| `/rules/[id]` | missing | pass | pass | pass |
| `/rules/[id]` | loading | pass | pass | pass |
| `/rules/statistics` | complete | pass | pass | pass |
| `/rules/statistics` | partial | pass | pass | pass |
| `/rules/statistics` | nulls | pass | pass | pass |
| `/rules/statistics` | error | pass | pass | pass |
| `/rules/statistics` | no-instance | pass | pass | pass |
| `/rules/statistics` | loading | pass | pass | pass |
| `/searches` | normal | pass | pass | pass |
| `/searches` | empty | pass | pass | pass |
| `/searches` | error | pass | pass | pass |
| `/searches` | loading | pass | pass | pass |
| `/searches/[id]` | truncated | pass | pass | pass |
| `/searches/[id]` | empty-executions | pass | pass | pass |
| `/searches/[id]` | missing | pass | pass | pass |
| `/searches/[id]` | loading | pass | pass | pass |


### The 18 failures

Home, states `normal`, `dash-error`, `offenses-not-collected`, and `eps-not-collected`, at 1440×900, 1280×800, and 1024×768. Measurement: 11 inline `style` attributes, all on `div.dg-item`, for example `--dg-x: 1; --dg-y: 1; --dg-w: 3; --dg-h: 7;`. `widgets/DashboardView.tsx` `cell()` sets those custom properties and comments that they are the one accepted inline style in the widget system (unitless grid numbers). Home `loading` has 0 inline styles and passes. Document widths are equal (1440/1440, 1280/1280, 1024/1024). Proposed owner: `frontend/src/widgets/DashboardView.tsx`. A route stylesheet cannot remove a React `style` attribute. Left as a finding.

`/admin/outage-policy`, states `normal` and `summary-403`, at all three viewports. Measurement: one `.nowrap` element computes `white-space: normal`. The element is `<time class="cell-sub nowrap">` with text `10-14 13:40 UTC` (`SourceTable.tsx` around the changed-by cell). The winning rule is shared CSS in `frontend/src/app/globals.css`:

```css
.fleet-table td .cell-sub {
  max-width: 26ch;
  white-space: normal;
}
```

That selector (0,2,1) beats `.nowrap` (0,1,0). The outage route has no colocated stylesheet (only `page.tsx` and the tables; they import shared `controls.css`). Adding a stylesheet and an import would be a new file plus a component change. Proposed owner: `globals.css`, one rule `.fleet-table td .nowrap { white-space: nowrap; }` after the `.cell-sub` rule. Not applied.

On the six outage PNGs the timestamp sits on one line under `a.rivera` at 1440, 1280, and 1024. The string does not wrap. The computed style still fails the `.nowrap` check.

### Local table scroll (passes the page check)

`/admin/search-cadence` `normal` at 1024×768 only. Table "Search cadence by family": `scrollWidth` 790, `clientWidth` 780, `hiddenColumns` empty. `png/admin_search-cadence__normal__1024x768.png` shows every column, including "Searches in last run" with values 4 through 10, at the start position. The 10px is inside the table region. The page `doc` is 1024/1024.

CSS-hidden headers are recorded separately and are not scrolled-out columns. At 1024 the anomalies list hides Deviation, Severity, Started, and Evidence (`cssHiddenColumns`); the same facts sit in the remaining cells. Behavior sources hides Detail the same way.

### What the failing captures show

Opened all 18 failing PNGs.

Home at 1440 and 1280 (`home__normal`, `home__dash-error`, `home__offenses-not-collected`, `home__eps-not-collected`): four KPI cards in a row, Customize is available, fleet EPS chart has an EPS axis and a UTC axis, recent offenses list 5 of 16 when analytics is present. `dash-error` adds the notice "Saved dashboards could not be loaded: the server answered HTTP 500. This is the SOC Overview template, and changes here are not saved." `offenses-not-collected` shows an em dash and "Not collected. No offense snapshot has been collected yet." on the open-offenses card and again under Recent offenses. `eps-not-collected` replaces the fleet line with "No step of 24 has every in-scope source-interval complete. No line is drawn; a step that was not measured is not zero traffic." No horizontal page scroll.

Home at 1024, same four states: the KPI cards stack in one column. The header control reads "Widen the window to customize". The template description wraps in full: "24 h view of fleet volume, source health, anomalies and offenses. Times are UTC." Partial collection stays labeled (20,377 of 21,600 intervals). The same honesty sentences appear for the dash error, the missing offense snapshot, and the unmeasured EPS line. Top-source names truncate with an ellipsis inside the name cell (`Perimeter Firewall …`), which is the existing one-line name treatment, and the class and state sit under the name.

Outage `normal` at 1440, 1280, and 1024: eleven class rows, Firewall and Other are Record only, source override PaSeries @ pa-fw-02 is Alert, 3 buckets / 15 min, changed by a.rivera, time `10-14 13:40 UTC` on one line. At 1024 the Firewall row's Edit and Use default stack; the page still fits.

Outage `summary-403` at 1440, 1280, and 1024: the notice "The source counts are not shown: your role cannot read the fleet summary (permission read:fleet). The policy below is complete." Only the saved Firewall class row remains, source overrides is an em dash, and the same PaSeries timestamp is on one line. The Firewall row still shows 12 under Monitored sources covered. That 12 is `governed_monitored_sources` on the saved policy row (`outage.ts` `classLines`), which the page keeps when the fleet summary is missing. The unmeasured override count is the em dash. The notice reads more broadly than that column. Proposed owner: the outage page copy. Not a CSS defect.

### One 1024×768 capture per route

| Route | PNG | What was on the page |
| --- | --- | --- |
| `/` | `png/home__normal__1024x768.png` | SOC Overview, stacked KPIs, "Widen the window to customize", charts with units, recent offenses populated. |
| `/admin` | `png/admin__normal__1024x768.png` | Five links: cadence, classification, outage, WAF, IPS. Fits the viewport height. |
| `/admin/classification-rules` | `png/admin_classification-rules__normal__1024x768.png` | Order of decision, empty group-rules notice, type and name rules with patterns wrapping in the cell. |
| `/admin/ips-sources` | `png/admin_ips-sources__normal__1024x768.png` | Three rows: no recent events, query failure, Cisco FTD perimeter Valid and Collecting. Times stay on one line. |
| `/admin/ips-sources/[logSourceId]` | `png/admin_ips-sources_logSourceId___valid__1024x768.png` | Cisco FTD — perimeter, mapping form, 0.2% Unknown-signature warning, Block 3,200 / Allow 6,780. Long page, no sideways overflow. |
| `/admin/outage-policy` | `png/admin_outage-policy__normal__1024x768.png` | Classes and the one source override. Timestamp on one line. See the nowrap finding. |
| `/admin/search-cadence` | `png/admin_search-cadence__normal__1024x768.png` | All seven families, last column visible (4–10). Footer explains "Not run since upgrade" and "Not counted". |
| `/admin/waf-sources` | `png/admin_waf-sources__normal__1024x768.png` | Three rows: no recent events, query failure, WAF edge collecting. Names wrap. |
| `/admin/waf-sources/[logSourceId]` | `png/admin_waf-sources_logSourceId___valid__1024x768.png` | WAF edge — eu-west, Version 4 Valid Collecting, field table, action values BLOCK 1,840 / ALLOW 8,000. |
| `/alerts` | `png/alerts__normal__1024x768.png` | Four alerts. Long titles wrap. Times stay one line. A resolved row uses an em dash for last seen. |
| `/alerts/[id]` | `png/alerts_id___normal__1024x768.png` | Volume spike evidence, lifecycle, acknowledge form, three notification rows including FAILED and DEAD_LETTER. |
| `/anomalies` | `png/anomalies__normal__1024x768.png` | 1–25 of 39. Deviation, severity, started, and evidence are folded into the row. Lower bounds use ≥. |
| `/anomalies/[id]` | `png/anomalies_id___ips-insufficient__1024x768.png` | Destination port 3022/tcp. Notice says INSUFFICIENT_DATA and that no expected band is drawn. The spike is still drawn. Lifecycle NORMAL → CANDIDATE → OPEN. |
| `/behavior` | `png/behavior__normal__1024x768.png` | 4 open, 1 silent, 2 high deviation, 2 insufficient data. Baseline notice: a zero here is the absence of a verdict. One source name ends in an ellipsis (`Windows Domain…`). |
| `/behavior/sources` | `png/behavior_sources__normal__1024x768.png` | Long hostnames wrap. Times stay one line. 1–25 of 33. Detail column is CSS-hidden. |
| `/behavior/sources/[id]` | `png/behavior_sources_id___partial__1024x768.png` | Perimeter Firewall — Istanbul, 182.40 vs 63.80 EPS, 2.86x. Chart gaps are labeled as not zero traffic. Collection health: 3 of 72 intervals have no bucket and 9 are partial. The anomaly table prints evidence status `READY`, which is not a real evidence enum. It is residue in the UI-5.1 fixture; the page shows the value it was given. |
| `/behavior/waf` | `png/behavior_waf__normal__1024x768.png` | Five sources: VALID, STALE, INVALID, UNVALIDATED, and a second VALID. The long hostname wraps. Lower bounds use ≥. Pagination 1–5 of 5. |
| `/behavior/waf/sources/[id]` | `png/behavior_waf_sources_id___partial__1024x768.png` | Customer Portal WAF, VALID, 200 of 200 endpoints. Rows include INSUFFICIENT_DATA, Not evaluated, and a retired endpoint. Paths wrap. |
| `/config-changes` | `png/config-changes__not-collected__1024x768.png` | Only the notice that configuration changes are not collected. No table and no zero. |
| `/coverage` | `png/coverage__normal__1024x768.png` | Six status cards (2/1/1/1/1/1). Technique names and evidence wrap. Rule-centric and data-source views follow. |
| `/fleet` | `png/fleet__normal__1024x768.png` | Class rows stack "Top sources by events" onto the next line. Other says Not measured, with no complete bucket in the window. |
| `/fleet/catchall` | `png/fleet_catchall__normal__1024x768.png` | Three senders. Long hostnames wrap. One 24 h volume is `unknown` where no stored list exists. Selected bucket says Exact, 3,570 events. Not-measured buckets are shaded. |
| `/fleet/sources` | `png/fleet_sources__normal__1024x768.png` | Six sources. Public API Gateway shows the name/type conflict with New, Set class, and Mark reviewed. Unclassified DNS says no rule matched. |
| `/log-sources` | `png/log-sources__normal__1024x768.png` | Seven rows fit the viewport. The long VPN name wraps. That row's last event is `never`, and its status is `unknown`. |
| `/log-sources/[id]` | `png/log-sources_id___normal__1024x768.png` | Perimeter Firewall — Istanbul, health 92/100, last event 2026-10-14 17:59:40 UTC, QRadar status SUCCESS. |
| `/offenses` | `png/offenses__normal__1024x768.png` | 30 total. Descriptions wrap. Null offenses (7403 and others) use em dashes. Pagination 1–25 of 30. |
| `/offenses/[id]` | `png/offenses_id___nulls__1024x768.png` | Offense 7401, "No description." Magnitude, severity, credibility, and relevance are em dashes. Status OPEN, age 8h 2m. Entities say "None recorded." History explains that a second snapshot is written only when the offense changes. |
| `/rules` | `png/rules__normal__1024x768.png` | Cards HEALTHY 9, DEGRADED 5, SILENT 4, INSUFFICIENT DATA 5, NEVER OBSERVED 5, DISABLED 4, NOISY 5. Banner: 5 rules have no health verdict yet. Meaning text wraps. Some later rows show an em dash in Health. Pagination 1–25 of 40. |
| `/rules/[id]` | `png/rules_id___healthy__1024x768.png` | Repeated authentication failures, HEALTHY, capacity and health history. A missing capacity cell is an em dash. |
| `/rules/statistics` | `png/rules_statistics__partial__1024x768.png` | "PARTIAL. Only 160 of 168 windows in the span are complete (3 missing, 3 truncated, 2 failed)." Match counts that are lower bounds use ≥. Type sits under the rule name. |
| `/searches` | `png/searches__normal__1024x768.png` | Four stored searches. A missing category is an em dash. The custom schedule wraps inside the cell. |
| `/searches/[id]` | `png/searches_id___truncated__1024x768.png` | Failed logons burst by source. Execution rows include ≥ 500, a breached threshold, and "truncated, count not recorded". The trend note says the line is broken where no count was stored. |

The shell status pill reads Unknown on every capture opened. `ConnectionStatus` paints the worst signal, and a neutral collection row outranks an ok backend check, so the pill can read Unknown while the page data is present. That is a status-pill fact, not a page-overflow defect.

### Orchestrator checks

1. Saved dashboard "Night shift", about 60px between the title and the description at 1024×768. Seen, on a UI-5.9 capture, not in this matrix. The UI-6.1 fixture returns an empty dashboard list, so none of the 558 PNGs is Night shift. I opened `/home/efe/Documents/QRadar-Dash-work/ui59/png/after4/p9-detection-health-1024x768.png` (h1 "Night shift" in that folder's `measurements.json`). The title ink occupies y=85 through y=105. The description ink starts at y=159. Rows y=106 through y=158 are empty: 53px between the title and "Quiet-hours view: volume, anomalies and the newest findings." The description then wraps, and the second line is "findings." On our own `png/home__normal__1024x768.png` that vertical slot holds the "Template, not saved" badge, and the template sentence sits directly under the badge. Proposed owner: `frontend/src/components/home/HomeDashboard.tsx`, which always passes a `meta` fragment into `PageHeader`, together with `.home .page-meta { min-height: var(--sp-6); }` in `frontend/src/widgets/dashboard.css` (24px) and the 8px `margin-top` on `.page-meta` and `.page-desc` in `globals.css`. Shared CSS and a component. `frontend/src/app/page.tsx` has no colocated stylesheet. Not edited.

2. Dashboard description clipped with "…" at 1024×768. Not seen. On the Night shift PNG the description wraps and ends with the word "findings." On `png/home__normal__1024x768.png` the template line wraps and ends "Times are UTC." Neither ends in an ellipsis.

3. `.nowrap` times on the outage-policy page. Seen, as the six failing measurements above. Computed `white-space` is `normal`. On the PNGs the time stays on one line.

## Gates

No stylesheet changed, so `npm ci`, `npm run lint`, `npm run typecheck`, `npx vitest run --no-cache`, and a fresh `npm run build` were not run. The production build already on disk is the one that was measured (`logs/build.log`: compiled successfully in 10.7s).

At the end of this report nothing was listening on 3161, 8161, or 9161. No new server was started for this write-up.

## Open points

- Home's 11 `.dg-item` custom properties fail the inline-style check and are the documented grid exception. Page overflow still passes.
- Outage `time.cell-sub.nowrap` loses to `.fleet-table td .cell-sub` in shared CSS. The route has no stylesheet to hold a one-line fix.
- Night shift's empty header band (53px at 1024 in the UI-5.9 PNG) is outside this matrix because dashboards were an empty list. The description there is not ellipsised.
- Evidence status `READY` on the behavior-source anomaly rows is fixture residue.
- The shell pill reads Unknown on these captures.
- Catch-all has no `NOT_COLLECTED` scene in the merged fixture. The normal capture shows `unknown` and shaded unmeasured buckets, and the selected bucket is Exact.
- `/admin/outage-policy` `summary-403` still shows 12 monitored sources on the saved Firewall row while the notice says source counts are not shown. The override count is an em dash.
