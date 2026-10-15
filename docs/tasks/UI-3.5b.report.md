# UI-3.5b report

## 1. Files changed

Modified (all inside the spec's allowed files):
- `frontend/src/widgets/types.ts`: `ConfigSchema` fields are a static list or `from` an `OptionsSource` (one existing GET read, optionally depending on another key); `isReadField`.
- `frontend/src/widgets/Configure.tsx`: `ReadField` (read through `useApiResource`, loading/failed/forbidden/empty states, client-side filter + listbox over a long list, first-page note), cascade clearing (`setConfigKey`), Apply disabled until every read key holds an id its read returned.
- `frontend/src/widgets/optionSources.ts` (new): the three option reads (monitored sources, WAF sources, one WAF source's endpoints).
- `frontend/src/widgets/registry.ts`: `waf.endpoint_traffic` and `chart.eps_observed_expected` added (live, with config, ranges, sources, requires); `ips.source_anomalies` becomes live "Recent IPS anomalies"; `live()` takes `config`/`defaultConfig`/function drill-down.
- `frontend/src/widgets/Catalog.tsx`: duplicate guard only for widgets without config keys (comment updated); routes shown as `{id}`.
- `frontend/src/widgets/runtime.tsx`, `DashboardView.tsx`: `onConfigure` in the widget runtime, set in customize mode only, so an unconfigured widget can offer Configure.
- `frontend/src/widgets/dashboard.css`: drawer field states and filter field.
- `frontend/src/lib/api.ts`: additive read helpers (`windowBounds`, `BucketSeries`, `homeReads.wafEndpointsOf / wafEndpointSeries / wafEndpointCells / sourceSeries / sourceCells / activeIpsAnomalies`).
- `frontend/src/components/waf/WafRequestChart.tsx`: optional `compact` prop (requests panel alone, fills its container, no coverage panel). Default rendering unchanged; its existing tests pass untouched.
- `frontend/src/app/tokens.css`: new tokens `--table-col-figures`, `--cw-chart-h`.
- `frontend/src/components/catalog/catalog.css`: `.cw-config`, `.cw-col-figures`, `.cw-tall`.

New: `components/catalog/{WafEndpointTraffic,SourceEps,SourceEpsChart,RecentIpsAnomalies,ConfigNotice}.tsx`, `subject.ts`, `ipsAnomalies.ts`, `subjectFixtures.ts`; tests `subjectCharts.test.tsx`, `ipsAnomalies.test.tsx`, `widgets/Configure.options.test.tsx`, `widgets/DashboardView.subjects.test.tsx`, `lib/widgetReads.test.ts` (a test of the new `api.ts` reads, placed in `lib/`).

**Existing-test edits (named, as the spec asks for the first; the other two follow from the spec itself):**
1. `registry.test.ts`: the exact live-type list gains the three types (as allowed). Consequences of the spec: the blocked list loses `ips.source_anomalies`, the test title says "one blocked one", and the assertion `ips.source_anomalies blockedBy === "the IPS analyst API is being built"` is replaced by `status === "live"` and `blockedBy` undefined (the widget is no longer blocked, so the old assertion cannot hold).
2. `registryRange.test.ts`: the "exactly the five widgets with a range" list gains `waf.endpoint_traffic` and `chart.eps_observed_expected` (now seven). The spec requires `1h | 24h | 7d` on both, which this test pins. **The spec only authorised the first edit; please confirm this one.**

No other existing test was changed, weakened or skipped.

## 2. What was done

- **Options from a read.** Routes checked against `backend/app/api/routes/anomalies.py` and `schemas/anomaly.py`: `/behavior/sources` is a plain list (no paging); `/behavior/waf/sources` and `.../sources/{id}/endpoints` are paged (page capped at `api_max_page_size`, 200); WAF metrics are paged (limit capped at 200, so a 24 h window is 2 pages and 7 d is 11, bounded at 12 then flagged TRUNCATED); source metrics are a list with `limit` up to 5000 (a full page is flagged TRUNCATED); baselines `metric=request_count` / `average_eps`. The fixture serves exactly these shapes.
  - The drawer requests each read once (shared with widgets through `useApiResource`'s key); the filter only appears above 8 options, narrows in the browser, is not a query, a body or part of the stored config (test asserts every request URL; the browser check found 0 requests containing the filter text). Long lists are a listbox beside the filter so the narrowed options are in view.
  - Loading, failed (what failed, HTTP status, what to do), forbidden and empty (what is empty and why) are text states; Apply is disabled in each, and until each read key holds an id the read returned. Changing the WAF source clears the endpoint. The layout stores ids only (`{waf_source, endpoint}`, `{source}`).
- **`waf.endpoint_traffic`.** Reuses `WafRequestChart` (compact) with the seasonal band from `/baselines`; the title is `METHOD /path · host` read from the source's endpoint list (cache shared with the picker), plus the range; drill-down to `/behavior/waf/sources/{id}` (there is no per-endpoint page). Window comes from the effective range as `since`/`until` computed when the read runs; the read key names the preset (not the instants), so an auto refresh keeps the chart on screen.
- **`chart.eps_observed_expected` (source mode).** `SourceEpsChart` uses `lib/timeseries` (PARTIAL/MISSING = null, gaps shaded, lone points marked, no bridging) with a per-bucket seasonal median and p05-p95 band; drill-down `/behavior/sources/{id}`.
- **States of both:** not configured ("Choose an endpoint" / "Choose a log source": a sentence in read mode, the sentence plus a Configure button while customizing; it reads nothing and draws nothing), loading, failed, forbidden, empty window (absence of collection, not zero), INSUFFICIENT_DATA (no bucket in a reliable cell: line drawn without a band, pill + reason), PARTIAL ("n of m stored buckets collected", shaded), TRUNCATED, and gone (series read 404, or the complete list no longer holds the id; a truncated endpoint list never proves "gone"). A gone id never draws another subject.
- **`ips.source_anomalies`** is live as "Recent IPS anomalies": two reads (`subject_kind=IPS_ENTITY` and `IPS_SOURCE`, `active_only`, limit 5), merged by detection time, cut to 5 (the top-n-of-the-union argument is a comment in `ipsAnomalies.ts`), "n of N" from the two totals, rows in the detector's unit (`ipsFigures`/`formatFigure`), never EPS. If either read fails the widget fails (it will not claim the latest of half a union). `/anomalies` supports one `subject_kind` only, so the header link goes to the entity list and the note links the source-level list.
- **Duplicates.** The guard now applies only to widgets without config keys.

## 3. Gates (from `frontend/`, `~/.local/node/bin` on PATH)

- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit` exit 0, no output
- `npx vitest run --no-cache`: `Test Files  167 passed (167)` / `Tests  2399 passed (2399)`
- `npm run build`: `✓ Compiled successfully in 4.1s` / `✓ Generating static pages (25/25)`

**Break-its** (all six the spec lists, each: break, confirm failure, restore; log `/home/efe/Documents/QRadar-Dash-work/ui35b/logs/breakits.log`):
1. filter text reaches a URL: 2 tests failed (`Configure.options.test.tsx`).
2. gone id draws another subject (gone check disabled): 2 failed (`subjectCharts.test.tsx`).
3. not configured renders a chart: 2 failed.
4. IPS merge drops the newest row: 8 failed (`ipsAnomalies.test.tsx`).
5. IPS row says EPS: 1 failed.
6. duplicate guard removed for widgets without config (second copy no longer refused): 1 failed (`DashboardView.subjects.test.tsx`).
Restored each time; the suite is green afterwards.

## 4. Screenshots and measurement

Fixture: a copy of the UI-3.3b fixture at `/home/efe/Documents/QRadar-Dash-work/ui35b/fixture/server.py` (ports 8136/3136/9136), extended with two WAF sources, 28 + 3 endpoints, endpoint and source metrics (paged as the backend pages them, honouring `since`/`until`), 168-cell baselines (some hours unreliable), an INSUFFICIENT_DATA endpoint, an empty-window endpoint, PARTIAL and absent buckets, IPS anomalies of both kinds, and a `/__control/gone` switch. Widgets were placed and configured through the real catalog and drawer.

PNGs in `/home/efe/Documents/QRadar-Dash-work/ui35b/png/` (I looked at each):
- Home with the widgets placed and configured: `home-new-widgets-1440x900-1.png`, `-2.png`, `home-new-widgets-1280x800-1.png`, `-2.png`, `home-new-widgets-1024x768-1.png`, `-2.png`
- Drawer, endpoint picker filtered: `configure-endpoint-filtered-1440x900.png`
- Not configured: `not-configured-customize-1440x900.png`, `not-configured-read-1440x900.png`
- Gone ids (after placement): `gone-1440x900.png`
- Expanded: `expand-waf-endpoint_traffic-1440x900.png`, `expand-chart-eps_observed_expected-1440x900.png`, `expand-ips-source_anomalies-1440x900.png`
- Empty window and PARTIAL: `states-empty-partial-1440x900.png`
(The same directory also holds the older Home PNGs that `verify.mjs` regenerates.)

`verify.mjs` (`/home/efe/Documents/QRadar-Dash-work/ui35b/verify.mjs`, copied from the UI-3.3b one with the UI-3.5b section added; run log `logs/verify-run.txt`, JSON `logs/ui35-output.json`) exited 0: all old checks (drag, resize, keyboard, customize density, range, expand, configure) passed, and the new section:
```
UI-3.5b checks: filter {optionsBefore: 29, optionsAfter: 4, "login", requestsContainingFilter: 0};
  expected404Logs: 2 (the two gone reads); goneCanvases [0,0,1]
UI-3.5b density (new widgets), titleRowHeight / body scrollHeight = clientHeight, no overflow, 0 inner overflow:
  1440x900: WAF 77/369, EPS 53/393, IPS 24/530, WAF 53/393
  1280x800: WAF 77/369, EPS 77/369, IPS 24/530, WAF 49/369
  1024x768: all four title rows 24-53, bodyOverflow false
Page overflow 0 at all three viewports.
```
Problems found by looking at the PNGs and fixed: the filter field rendering 260 px tall (a global `.field` rule), the IPS figures column colliding with the Investigate link (new token), IPS widget overflowing at 1280 (default height 15 to 17), a captured-beyond-viewport artefact (captures are now real viewport captures), the highlighted-looking first row of the listbox.

## 5. Open points

- **registryRange.test.ts edit needs your confirmation** (see section 1).
- No fleet-wide endpoint list exists, so the endpoint picker is cascaded and shows the first 200 endpoints of a source; the drawer says when more exist. A 7 d WAF window needs 11 metric pages; beyond 12 pages the chart says TRUNCATED.
- There is no read for a single WAF endpoint, so the endpoint's title comes from the source's endpoint list; if the endpoint is past the first 200 the title falls back to "WAF endpoint traffic".
- The Expand dialog's own title is the generic widget title (the dialog is outside the widget); the subject is named on the first line of the dialog body.
- The registry's `sources` for templated routes use `${id}` (the existing API-routes test needs that form); the catalog shows them as `{id}`.
- The app ships no favicon, so a fresh Chrome profile logs a favicon 404; `verify.mjs` ignores that one entry by URL.
- The fixture has a pinned clock, but the browser uses the real one; the fixture builds buckets for whatever `since`/`until` the page asks, so the charts are deterministic in shape, not in absolute time.
- All processes stopped by PID (fixture 167954, Next 214375, Chrome 166223). Nothing written under `/tmp`. Nothing committed.
