# UI-6.3 report — performance of the dashboard

Task: [`docs/tasks/UI-6.3.md`](UI-6.3.md) (Phase 6 QA; thresholds from `docs/UI-REDESIGN-PLAN.md`
UR.8.3 and the UI-6.3 row of UR.7 Phase 6). This task **measures and reports**. No product file
was changed (see "Findings" for why nothing was fixed).

Method, fixture and re-run instructions live outside the repository, as the task requires:
`/home/efe/Documents/QRadar-Dash-work/ui63/README.md`, harness `perf.mjs`, fixture
`fixture/server.py`. Raw numbers: `…/ui63/logs/perf.json`, `…/logs/perf-full.log`,
`…/logs/probe.json`, `…/logs/refresh2.json`, `…/logs/refresh3.json`, `…/logs/refresh4.json`,
`…/logs/refresh5.json`, `…/logs/cors.json`, `…/logs/flicker.json`, `…/logs/heap.json`,
`…/logs/heap2.json`, `…/logs/heap3.json`, `…/logs/build.log`, `…/logs/bundle.txt`.

Everything below is 1440×900, headless Chrome
(`/usr/bin/google-chrome --headless=new --no-sandbox --user-data-dir=…/chrome-profile`, CDP 9163).
**`--no-sandbox` was needed**: Chrome's own sandbox cannot start inside this agent's filesystem
sandbox. That is the only deviation from a stock browser launch; SELinux stayed enforcing and
nothing was relabelled.

## 1. What was measured, and on what

| Piece | Value |
|---|---|
| Fixture API | `ui63/fixture/server.py` (copied from `QRadar-Dash-work/t098/fixture/`), port 8163, clock pinned to 2026-10-14T18:00:00Z; it allows the origin `http://127.0.0.1:3163` only, so every API read in every run below is cross-origin (§4.4) |
| App | production build (`npm run build`) served by `next start -p 3163`, `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8163/api/v1` |
| Dashboard A | SOC Overview template, `/?dashboard=template` — 11 widgets, 3 charts |
| Dashboard B | 24 widgets, `seed=heavy` — one of every live registry type (22) + 2 chart duplicates → 7 chart bodies |
| Dashboard C | 24 widgets, `seed=charts` — 10 chart bodies (the "at least 8 charts" case) |
| Runs | 5 cold (fresh page target + `Network.clearBrowserCache`) and 5 warm (warm browser cache) per dashboard |

The registry holds 22 live widget types, so 24 widgets can hold all of them, but only **five** live
types are charts: a 24-widget dashboard cannot hold every live type *and* 8 chart bodies. B is the
"every live type" reading of the task, C is the "at least 8 charts" reading; both were measured.

Two details of the method, because they bound what each number means:

- **Cold** = a new page target with the browser cache cleared, so the JS chunks and the fixture's
  JSON are all fetched. **Warm** = the same target re-navigating with the cache warm; the fixture
  sends `Cache-Control: no-store`, so every API response is re-fetched in both.
- **Time to the first widget's content** is a `MutationObserver` installed before any page script
  runs: `performance.now()` the first time a `.tw-body`/`.kpi-body` holds text that is not a
  `Loading …` line. That body can be a KPI, a table or a chart; it is the first widget, not the
  first chart.

## 2. Time to the first widget's content — budget < 2000 ms

| Dashboard | Load | Median | Worst | Budget | Result |
|---|---|---|---|---|---|
| SOC Overview template | cold | 191 ms | 442 ms | 2000 ms | **pass** |
| SOC Overview template | warm | 58 ms | 81 ms | 2000 ms | **pass** |
| 24 widgets (7 charts) | cold | 424 ms | 447 ms | 2000 ms | **pass** |
| 24 widgets (7 charts) | warm | 71 ms | 78 ms | 2000 ms | **pass** |
| 24 widgets (10 charts) | cold | 421 ms | 433 ms | 2000 ms | **pass** |
| 24 widgets (10 charts) | warm | 85 ms | 92 ms | 2000 ms | **pass** |

Per run, ms:

| Dashboard | Cold (5 runs) | Warm (5 runs) |
|---|---|---|
| template | 442, 191, 182, 160, 238 | 75, 49, 81, 58, 48 |
| 24 widgets (7 charts) | 411, 419, 426, 424, 447 | 78, 70, 71, 67, 73 |
| 24 widgets (10 charts) | 421, 419, 433, 426, **194** | 72, 85, 92, 86, 75 |

The worst cold load is **442 ms**, well inside the 2000 ms budget; the 24-widget dashboard costs
about **+230 ms** over the 11-widget template cold and is *faster* warm than the template is warm.
The 194 ms outlier was a cold run of the 10-chart dashboard where the first widget's chunk and data
won the race earlier than in the other four; it is reported rather than smoothed away.

## 3. Long tasks — budget: no long task above 100 ms during a drag

`PerformanceObserver` `longtask`, in page time. During the load:

| Dashboard | Load | Long tasks (median / worst count) | Longest (median / worst) | Result |
|---|---|---|---|---|
| template | cold | 0 / 1 | 0 / 62 ms | pass |
| template | warm | 0 / 0 | 0 / 0 ms | pass |
| 24 widgets (7 charts) | cold | 1 / 1 | 66 / 75 ms | pass |
| 24 widgets (7 charts) | warm | 0 / 0 | 0 ms | pass |
| 24 widgets (10 charts) | cold | 1 / 1 | 58 / 86 ms | pass |
| 24 widgets (10 charts) | warm | 0 / 0 | 0 ms | pass |

Per run, longest task in ms (cold): template `0, 0, 54, 0, 62`; 7-chart `60, 59, 75, 66, 72`;
10-chart `0, 0, 69, 86, 58`. Warm loads produced no long task at all.

Drag in Customize mode (three drags of the widget's header handle, ~800 ms of pointer movement
each, `Input.dispatchMouseEvent`), on the 24-widget dashboard:

| Drag | Moved? | Long tasks | Longest | Result |
|---|---|---|---|---|
| 1 | yes (KPI moved right and down) | 0 | 0 ms | **pass** |
| 2 | yes | 0 | 0 ms | **pass** |
| 3 | no (the drag ended over the same cell) | 0 | 0 ms | **pass** |

**No long task above 100 ms during a drag: pass, with zero long tasks at all in any of the three.**
Scroll to the bottom of the 24-widget dashboard (4740 px of scroll): 0 long tasks as well.

## 4. Requests

### 4.1 Up to network idle (no in-flight request for 600 ms)

| Dashboard | Load | Requests (median) | Bytes (median) | Worst bytes | Result |
|---|---|---|---|---|---|
| template | cold | 100 | 543 kB | 545 kB | reported |
| template | warm | 100 | 72 kB | 78 kB | reported |
| 24 widgets (7 charts) | cold | 154 | 996 kB | 997 kB | reported |
| 24 widgets (7 charts) | warm | 154 | 487 kB | 492 kB | reported |
| 24 widgets (10 charts) | cold | 165 | 1276 kB | 1280 kB | reported |
| 24 widgets (10 charts) | warm | 165 | 776 kB | 778 kB | reported |

UR.8.3 gives no budget for the load itself, so these are reported, not judged. UR.8.3's own model
was "about 9 distinct requests" for the default Home's 11 widgets *per refresh*; the load count is
larger because it includes the JS chunks (44 scripts for the template, 59 for the 24-widget page),
the fonts and the API reads — see §4.3 for the split and §4.2 for the per-refresh figure, which is
the one UR.8.3 actually budgets. Half of the "API requests" above are CORS preflights (§4.4): the
template's `Fetch` count is 35, of which 30 are API requests ≈ 15 reads + 15 `OPTIONS`.

### 4.2 At idle, 60 s, no interaction — budget: no request at idle

Measured after the load reached network idle, on the warm page, with Refresh `Off`:

| Dashboard | Window | Requests | Result |
|---|---|---|---|
| template | 62.0 s | 0 | **pass** |
| 24 widgets (7 charts) | 62.0 s | 0 | **pass** |
| 24 widgets (10 charts) | 62.0 s | 0 | **pass** |

`document.visibilityState` was `visible` in every window, so the refresh timer was not paused by
tab visibility.

### 4.3 With Refresh set to 1 min, 60 s

| Dashboard | Widgets | Refresh select read back | Requests in 62 s | Distinct paths | Result |
|---|---|---|---|---|---|
| template | 11 | `1` | 31 | 13 | reported |
| 24 widgets (7 charts) | 24 | `1` | 69 | 27 | reported |
| 24 widgets (10 charts) | 24 | `1` | 83 | 33 | reported |

Every API read in that window appears once as the `GET` and once as the `OPTIONS` preflight in front
of it (§4.4), so the 69 requests of the 7-chart dashboard are 34 reads + 34 preflights + the one RSC
fetch. "Distinct paths" above counts **pathnames**, so a read whose query carries its window
(`/api/v1/anomalies?since=…&until=…`, `/api/v1/anomalies?active_only=true&limit=5`, …) folds into
one path; §4.4 counts URLs.

### 4.4 What one refresh is, by HTTP method

`probe-refresh.mjs` (`logs/probe.json`): the 24-widget dashboard with Refresh = 1 min, watched for
190 s and grouped into bursts (requests separated by more than 5 s):

| Burst | Requests | Distinct URLs | Bytes | RSC fetch |
|---|---|---|---|---|
| t = 60 s | 69 | 33 | 463 kB | 1 |
| t = 120 s | 69 | 33 | 463 kB | 1 |
| t = 180 s | 69 | 33 | 463 kB | 1 |

**One refresh per minute, as configured** — and the burst is not 69 reads. `probe-refresh3.mjs`
(`logs/refresh3.json`) recorded the method of each request in the t = 60 s burst:

| Method | Count | What it is |
|---|---|---|
| `GET` | 35 | 34 API reads + the one `router.refresh()` RSC fetch of the page |
| `OPTIONS` | 34 | a CORS preflight, one in front of each API read |
| total | 69 | |

So a refresh is **34 reads** — 32 distinct URLs, two of them read twice: `/api/v1/overview` (the top
bar's own read plus the two widgets that read it, `kpi.log_sources` and `health.log_sources`, which
share one request through `useApiResource`'s in-flight map) and `/api/v1/admin/search-cadence` (the
top bar plus `health.collection`). Every other URL is read exactly once. 1.4 reads per widget, with
no N+1 the dedupe could have prevented, and UR.8.3's "about 9 distinct requests" model is honoured
per widget-set rather than approached end to end: the 11-widget template reads 15 URLs
(§4.3's 31 requests = 15 reads + 15 preflights + the one RSC fetch) because the shell itself
accounts for five of them.

The other 34 requests are the tax Finding 3 describes: every read carries a preflight, and the
preflight is never reused. UR.8.3 budgets "client requests" without saying whether a preflight
counts; if it does, a 24-widget refresh is 69 requests, and if it does not, it is 34 reads over 32
distinct URLs. Both are stated here rather than one being chosen; Finding 3 gives the fix.

| Variant of the same burst (fixture flags, §Gates) | Requests | `GET` | `OPTIONS` |
|---|---|---|---|
| fixture as delivered (no `Access-Control-Max-Age`) | 69 | 35 | 34 |
| with `Access-Control-Max-Age: 600`, the header the product's own CORS middleware sends (`logs/refresh4.json`) | 69 | 35 | 34 |
| …and without `Cache-Control: no-store` on the preflight answer (`logs/refresh5.json`) | 69 | 35 | 34 |

Chrome re-preflights regardless of both headers, and `probe-cors.mjs` (`logs/cors.json`) isolates
why. The same URL, cross-origin, read three times, in four shapes:

| Request shape | Reads | Preflights |
|---|---|---|
| `Content-Type: application/json` + `cache: "no-store"` (what the app sends) | 3 | **3** |
| `Content-Type: application/json`, default cache mode | 3 | **1** |
| plain `GET`, default cache mode | 3 | **0** |
| plain `GET`, `cache: "no-store"` | 3 | **0** |

A bodyless `GET` **needs no preflight at all** — `application/json` is simply not one of the
Content-Type values CORS calls safelisted, so sending it turns a simple request into a preflighted
one — and `cache: "no-store"` on the request stops the browser reusing the preflights it did send.
Both come from the same two lines. Finding 3.

### 4.5 What the refresh looks like on screen

`probe-flicker.mjs` (`logs/flicker.json`): sampled every 100 ms for 30 s across one refresh and
35 s across the next, on the 24-widget dashboard.

| Sample window | Widget bodies in a `Loading …` state | ECharts instances seen | Top bar "Refreshed" clock |
|---|---|---|---|
| 300 samples (first refresh) | 0 (max) | 7 in every sample | advanced 07:41:59 → 07:43:01 UTC |
| 350 samples (second refresh) | 0 (max) | 7 in every sample | 07:43:01 UTC |

The refresh itself is **invisible**: no widget body returns to a loading line and no chart is
disposed and recreated, so the analyst sees only the top bar's clock move. The "old data stays
until the new answer lands" behaviour of `useApiResource` (its lines 16-23) is what the analyst
gets, in both windows. The cost of a refresh is network, not presentation.

## 5. ECharts instances

`document.querySelectorAll("[_echarts_instance_]")` — the attribute `echarts.init` sets and
`echarts.dispose` removes, exactly what `echarts.getInstanceByDom` counts.

### 5.1 Off-screen charts — UR.8.3: off-screen charts must not be created

| Dashboard | Instances before scrolling | After scrolling to the bottom | Widget bodies on the page | Result |
|---|---|---|---|---|
| template | 3 | 3 | 11 | **fail** |
| 24 widgets (7 charts) | 7 | 7 | 24 | **fail** |
| 24 widgets (10 charts) | 10 | 10 | 24 | **fail** |

Every chart instance exists **before any scrolling**: the 24-widget dashboard is 4740 px tall and
all 7 of its charts (10 on dashboard C) are already created while the viewport shows the first two
rows. See Finding 1.

### 5.2 Disposal on unmount

Navigate to `/behavior` (chart instances there are its own), then back, three times; instance count
and JS heap (`Performance.getMetrics` → `JSHeapUsedSize`, after `HeapProfiler.collectGarbage`):

| Dashboard | Start | Cycle 1 (away → back, heap) | Cycle 2 | Cycle 3 | Result |
|---|---|---|---|---|---|
| template | 3 | 0 → 3, 12.4 MiB | 0 → 3, 18.3 MiB | 0 → 3, 24.0 MiB | count pass, heap see Finding 2 |
| 24 widgets (7 charts) | 7 | 0 → 7, 19.6 MiB | 0 → 7, 28.9 MiB | 0 → 7, 38.2 MiB | count pass, heap see Finding 2 |
| 24 widgets (10 charts) | 10 | 0 → 10, 22.7 MiB | 0 → 10, 33.6 MiB | 0 → 10, 44.5 MiB | count pass, heap see Finding 2 |

### 5.3 Where the heap growth comes from

`probe-heap.mjs` (`logs/heap.json`) repeated the cycle with two controls:

| Cycle | Cycle 1 | Cycle 2 | Cycle 3 |
|---|---|---|---|
| 24 widgets → `/behavior` → 24 widgets: heap | 10.1 → 19.4 MiB | 19.4 → 28.7 MiB | 28.7 → 38.0 MiB |
| …DOM nodes | 2673 → 5346 | 5346 → 8019 | 8019 → 10692 |
| 24 widgets → 24 widgets (same route): heap | 38.0 MiB | 38.0 MiB | 38.0 MiB |
| …DOM nodes | 10692 | 10692 | 10692 |

Reloading the dashboard is **flat**; a cycle that visits another route retains **exactly +2673 DOM
nodes and ≈9.3 MiB** every time. The retained subtree is still reachable while the dashboard is on
screen, so it is detached — Finding 2. `probe-heap2.mjs` (`logs/heap2.json`) repeats the cycle for
five routes to say which ones, and §5.4 says whose nodes they are.

**Disposal itself is clean**: leaving the route drops the instance count to 0 (all charts of that
route gone) and returning restores exactly the same count, with no drift across three cycles. The
heap, however, grows by 5.8 / 9.3 / 10.9 MiB per leave-and-return cycle — Finding 2.

### 5.4 Which routes retain, and whether the retention is the app's

`probe-heap2.mjs` (`logs/heap2.json`), three leave-and-return cycles per route, starting from the
24-widget dashboard (2673 nodes, 10.6 MiB after the first load):

| Route visited | Nodes after return, per cycle | Heap after return, MiB | Growth |
|---|---|---|---|
| `/behavior` | 5346 → 8019 → 10692 | 19.4 → 28.7 → 38.0 | +2673 nodes / +9.3 MiB per cycle |
| `/offenses` | 13365 → 16038 → 18711 | 47.3 → 56.5 → 65.8 | +2673 nodes / +9.3 MiB per cycle |
| `/anomalies` | 18715 → 18715 → 18715 | 65.8 → 65.8 → 65.8 | flat |
| `/fleet` | 18715 → 18715 → 18715 | 65.8 → 65.8 → 65.8 | flat |
| `/behavior/sources` | 18715 → 18715 → 18715 | 65.8 → 65.8 → 65.8 | flat |
| `/` (the dashboard itself) | 18715 → 18715 → 18715 | 65.8 → 65.8 → 65.8 | flat |

Every one of those navigations was a **full document load** (`Page.navigate`), which is what a
reload or a typed URL does; the growth stops as soon as the sequence reaches `/anomalies`, and the
node delta is the *dashboard's own* node count (2673), not the visited route's. That is the
signature of the browser keeping the previous document alive, not of React failing to unmount, so
the same sequences were repeated with **client-side navigation** — `probe-heap3.mjs`
(`logs/heap3.json`), clicking the shell's `next/link` entries (24 `.dg-item` on `/` in every
sample):

| Round trip (soft navigation) | On `/behavior` | Back on the 24-widget dashboard `/` |
|---|---|---|
| start | — | 2673 nodes, 10.1 MiB, 7 instances |
| 1 | 3367 nodes, 12.4 MiB, 0 instances | 5370 nodes, 19.6 MiB, 7 instances |
| 2 | 3367 nodes, 12.4 MiB, 0 instances | 5370 nodes, 19.6 MiB, 7 instances |
| 3 | 3367 nodes, 12.4 MiB, 0 instances | 5370 nodes, 19.6 MiB, 7 instances |
| 4 | 3367 nodes, 12.4 MiB, 0 instances | 5370 nodes, 19.6 MiB, 7 instances |
| `/offenses` round trip 1 | 2945 nodes, 16.4 MiB, 0 instances | 5374 nodes, 20.2 MiB, 7 instances |
| `/offenses` round trip 2 | 2945 nodes, 17.0 MiB, 0 instances | 5374 nodes, 20.5 MiB, 7 instances |

Under real navigation the counts are **flat from the second round trip on** (5370 nodes and 19.6 MiB
for four consecutive visits), the instance count returns to exactly 7 every time, and nothing grows
on the visited route either. So: the client-side retention is **one** extra copy of the page tree
(~2700 nodes, ~9.5 MiB), taken once when the dashboard is first left and replaced rather than
added to afterwards; the unbounded growth in §5.2/§5.3 needs a full document load per cycle and
does not reproduce under the navigation an analyst actually performs. Finding 2 states both.

## 6. Bundle

`npm run build` (`logs/build.log`), Next.js 15.5.28:

| Route | Size | First Load JS |
|---|---|---|
| `/` | 22.5 kB | 140 kB |
| `/anomalies/[id]` | 17.4 kB | **314 kB** |
| `/behavior/sources/[id]` | 926 B | **294 kB** |
| `/searches/[id]` | 5.46 kB | 294 kB |
| `/fleet/catchall` | 17.2 kB | 292 kB |
| First Load JS shared by all | — | 104 kB |

The three heaviest routes are `/anomalies/[id]` (314 kB), `/behavior/sources/[id]` (294 kB) and
`/searches/[id]` (294 kB); `/fleet/catchall` is a fourth at 292 kB. All four are chart-bearing
detail routes and all four carry the ECharts chunk in their first load. `/` does not: its charts
are behind `next/dynamic`, and the `react-grid-layout` chunk is not in any route's first load
either.

ECharts modularity and map absence (`bundle.sh`, `logs/bundle.txt`): 47 chunks; exactly one chunk
contains ECharts (`9231-293c972368dff020.js`, 450,627 bytes).

| Check | Result |
|---|---|
| full-build-only chart types (`GaugeChart`, `TreemapChart`, `SankeyChart`, `SunburstChart`, `GraphChart`, `CandlestickChart`, `BoxplotChart`, `PictorialBarChart`, `RadarChart`, `ParallelChart`, `FunnelChart`, `ThemeRiverChart`, `TreeChart`, `MapChart`) | **absent** from every chunk |
| map code (`geoJSON`, `parseGeoJSON`, `GeoComponent`, `VisualMapComponent`) | **absent** from every chunk |
| `registerMap` | present once, as `core`'s feature stub (`utils("registerMap") → i && i(t,e,n)`): the API exists, the map feature behind it is not bundled |
| the ECharts chunk in the first load of | `/anomalies/[id]`, `/behavior/sources/[id]`, `/searches/[id]`, `/fleet/catchall` only |
| `react-grid-layout` chunk (`7732…js`, 65,309 bytes) | in **no** route's first load; fetched only when Customize opens |

## Findings

Four findings. **None was fixed**: each one is larger than "local to one widget or one hook", which
is the only kind of defect this task may fix, so all four go to the orchestrator with the
measurement, the cause and a proposed fix. The task's budgets (§2, §3, §4.2) all pass; F1 is a
rendering cost, F2 a retention cost, F3 a request-count cost and F4 a constraint that cannot be
met as written.

### F1 — Every chart is created on the dashboard, including every off-screen chart

**Measurement.** §5.1: on the 24-widget dashboard all 7 (seed `heavy`) or 10 (seed `charts`) ECharts
instances already exist when the viewport shows the first two rows, and the count does not change
when the page is scrolled to the bottom (4740 px of scroll). The template creates all 3 while 11
widgets are on a 5640 px page. UR.8.3 is explicit: "each is created on mount, disposed on unmount,
and **off-screen charts are not created** (`IntersectionObserver`)". There is **no
`IntersectionObserver` anywhere in `frontend/src`** (`grep -rn IntersectionObserver frontend/src`
→ 0 matches), so the mechanism the spec names is missing entirely.

**Cause.** The read grid renders every placed widget at once, and each widget body creates its
chart in a mount effect:

- `frontend/src/widgets/DashboardView.tsx:423-444` — the read-mode `.dg` grid maps over `placed`
  and mounts a `<PlacedWidget>` for every widget, with no visibility gate and no virtualization.
- `frontend/src/widgets/registry.ts:28-38,60-73` — each widget body is `next/dynamic` with
  `ssr: false`, so it is fetched *and mounted* as soon as React renders it. `next/dynamic` defers
  the chunk, not the mount; nothing here asks whether the widget is on screen.
- `frontend/src/components/home/useEChart.ts:33-45` — `echarts.init(el)` in an effect keyed only on
  `build`, with a `ResizeObserver`; the chart exists from the first commit.
- `frontend/src/components/waf/WafRequestChart.tsx:461-475` — the WAF chart has its own copy of the
  same effect (`echarts.init` → `ResizeObserver` → `dispose`).

**Cost.** 7-10 charts are built, laid out and kept alive for a viewport that can show two. On the
cold load this is most of the 59-script / 456 kB `Script` total (the ECharts chunk is 450,627 bytes,
§6) and is the main reason the 24-widget dashboard costs ~+230 ms over the template (§2). The
budget still passes, so this is a cost, not a breach.

**Proposed fix (orchestrator).** A shared `useInView` hook (`IntersectionObserver`, rootMargin of
about one screen) used by `DashboardView`'s read grid (or by `PlacedWidget`) to render the existing
text-only placeholder until the widget first comes near the viewport, then mount and keep it
mounted. One hook plus one call site; the charts' own effects stay as they are. Because it spans a
shared hook, the grid and possibly `registry.ts`, and because it changes first-paint behaviour on
every dashboard, it is not a UI-6.3 fix.

### F2 — Leaving and returning to a dashboard retains about one page tree

**Measurement.** §5.2/§5.3: with full document loads (`Page.navigate`, i.e. a reload or a typed
URL), the dashboard's node count grows by **exactly +2673 nodes and ≈9.3 MiB on every round trip**
to `/behavior` or `/offenses` (three cycles each, no sign of stopping), and the instance count
itself is clean. §5.4 shows the growth needs the full document load: repeated client-side visits
(clicking the shell's links, what an analyst does) settle at **5370 nodes / ~19.6 MiB**, flat for
four consecutive round trips, with the ECharts count back at exactly 7 every time; `/anomalies`,
`/fleet`, `/behavior/sources` and the dashboard itself were flat even with full loads. The one-time
cost of the first client-side departure is ~2700 nodes / ~9.5 MiB, and it is then replaced, not
added to.

**Cause.** Not attributable to one widget or one hook. The retained node count equals the
dashboard's *own* node count (2673), not the visited route's, and it is only reached with whole
document loads, so the first candidate is the platform (previous document retained) and the
one-time client-side step is the app-router's own tree. The dashboard is a client island that reads
through module-level state — `frontend/src/hooks/useApiResource.ts:64` keeps a module-level
`entries` map — and `frontend/src/components/home/HomeDashboard.tsx:242` adds a capture-phase
`document` click listener; either is a plausible holder of a stale subtree, but the probes cannot
tell which, and one heap snapshot pair would be needed to say (below).

**Impact.** ~9.5 MiB per dashboard visit in a long-lived session is a slow, bounded cost on a
24-widget dashboard and it does not grow without bound under normal navigation. It is worth a
look, not an incident.

**Proposed fix (orchestrator).** A dedicated heap-snapshot task: `HeapProfiler.takeHeapSnapshot`
after round trip 1 and round trip 2, then retainer comparison for the detached `.dg-item` /
`.tw-body` subtrees. Fix only what the retainers show. Do **not** "clean up" the module-level map
on a guess: `useApiResource.ts` deliberately holds the settled answer while a caller is mounted
(lines 5-11), and the clean instance counts in §5.3 are the evidence that disposal works today.

### F3 — Every API read pays a CORS preflight, and the preflight is never reused

**Measurement.** §4.4: a refresh of the 24-widget dashboard is 69 HTTP requests, of which **34 are
`OPTIONS` CORS preflights** in front of 34 reads (35 `GET`s including the RSC fetch). The same tax
is on every load (§4.1: the template's 30 API requests of a cold load are 15 reads + 15 preflights).
`probe-cors.mjs` (`logs/cors.json`) isolates it by reading one URL three times in each of four
request shapes: **3 preflights** for `Content-Type: application/json` + `cache: "no-store"` (the
app's shape), **1** for the header alone, **0** for a plain `GET` with either cache mode. Neither
`Access-Control-Max-Age: 600` (which the product's own middleware sends — `backend/app/main.py:52-58`
sets no `max_age`, so starlette's default applies) nor dropping `Cache-Control: no-store` from the
preflight answer changed the 34 (`logs/refresh4.json`, `logs/refresh5.json`): the client's own
`cache: "no-store"` is what stops the browser reusing them.

**Cause.** `frontend/src/lib/api.ts:38-41`:

```ts
const res = await fetch(`${baseUrl()}${path}`, {
  ...init,
  headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  cache: "no-store",
});
```

`Content-Type: application/json` on a bodyless `GET` is not a CORS-safelisted value, so the request
becomes a non-simple one and every read is preflighted; `cache: "no-store"` then keeps the browser
from reusing the preflight. `fleetRequest` (`lib/api.ts:1784-1787`) has the same two lines, so the
fleet reads behave the same way. This is shared by every route in the app.

**Impact.** In the topology this task had to measure — app and API on different ports, which is also
the dev topology — every read and every chart's bucket read costs two round trips, so a refresh is
69 requests instead of 35 and a cold load carries the same 2× (§4.1, §4.3). The cost is latency and
request count, not payload: the preflights are 0 bytes and add no `Fetch` bytes. **Against the
production topology it is zero**, because the SPA and the API are same-origin behind the reverse
proxy (`backend/app/main.py:50-51`), and a same-origin `GET` is never preflighted. So the finding is
"every number in §4.1-§4.4 that counts requests is about twice what production sees", with the dev
and harness cost on top being real but bounded to a 204 and a round trip.

**Proposed fix (orchestrator).** One of, in order of preference:

1. Send `Content-Type` only when the request has a body (`init?.body !== undefined`), which removes
   the preflight altogether — measured: a plain `GET` cross-origin, `no-store` and all, sends none.
   Its own tests pin both helpers' headers.
2. Keep the header and stop setting `cache: "no-store"` on the client: the API's own middleware
   already answers `Cache-Control: no-store` (`backend/app/main.py:60-67`), so the client flag is
   redundant, and without it a preflight is reused for its `Max-Age` (measured: 1 for 3 reads).

Both are in a shared client (`lib/api.ts`) used by every page, so this is not a one-widget or
one-hook fix and was not done here.

### F4 — The task's 24-widget seed cannot hold every live type and eight charts

**Measurement.** §1. The registry holds **22 live widget types**
(`frontend/src/widgets/registry.ts`: 22 `live({…})` entries, plus one `blocked` `map.geography`),
and exactly **5** of them render a chart body: `chart.eps_fleet_total` (`registry.ts:149-160`),
`chart.eps_by_class` (`:161-172`), `chart.anomaly_severity` (`:185-195`),
`chart.eps_observed_expected` (`:343-361`) and `waf.endpoint_traffic` (`:319-342`, the one whose own
`echarts.init` is at `WafRequestChart.tsx:465`). `MAX_WIDGETS = 24`
(`frontend/src/widgets/Catalog.tsx:26`). So 24 widgets can hold all 22 live types only by repeating
2 of them, and repeating charts is the only way to reach 8 chart bodies at all: the task's
"mixing every live widget type, at least 8 of them charts" is satisfiable only as *22 types + 2
chart duplicates*, never as *22 distinct types + 8 distinct charts*.

**Cause.** Not a code defect — a task constraint that predates the registry's final size. Both
readings were measured rather than chosen (§1, and `fixture/server.py:757-815`): `seed=heavy` is
the "every live type" dashboard (24 widgets, 7 chart bodies), `seed=charts` is the "≥ 8 charts" one
(24 widgets, 10 chart bodies, missing only `health.evidence`, `table.outage_policy`,
`waf.sources`).

**Proposed fix (orchestrator).** Record the two readings in UR.8.3 (or in the UI-6.3 row of the
planner) so a later task does not read "8 charts over every live type" as an unmet requirement.
If the redesign wants ≥ 8 *distinct* chart widgets on one dashboard, that is a catalog change, not a
measurement.

## Gates

**No product file was changed by this task** (see Findings), so the frontend gates the task makes
conditional on a fix — `npm run lint`, `npm run typecheck`, `npx vitest run --no-cache`,
`npm run build` — were **not required and not run**; `mypy`/`ruff` are the backend's and no backend
file was touched either. The only repository file written is this report. The commands that were
actually run, and their results:

| # | Command (from `frontend/`, `PATH=$HOME/.local/node/bin:$PATH`) | Result |
|---|---|---|
| 1 | `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8163/api/v1 npm run build` | exit 0; route table in §6; `logs/build.log` |
| 2 | `node perf.mjs` (ports 3163/8163/9163; 30 load runs = 3 dashboards × 5 cold + 5 warm, plus the scroll, drag, idle and refresh stages, `logs/perf-full.log`) | exit 0, "raw results: …/logs/perf.json"; all budgets pass (§2, §3, §4.2) |
| 3 | `node probe-refresh.mjs` | exit 0, `logs/probe.json` |
| 4 | `node probe-flicker.mjs` | exit 0, `logs/flicker.json` |
| 5 | `node probe-heap.mjs` | exit 0, `logs/heap.json` |
| 6 | `node probe-heap2.mjs` | exit 0, `logs/heap2.json` |
| 7 | `node probe-heap3.mjs` | exit 0, `logs/heap3.json` |
| 8 | `node probe-refresh2.mjs` | exit 0, `logs/refresh2.json` |
| 9 | `node probe-refresh3.mjs` | exit 0, `logs/refresh3.json` |
| 10 | `node probe-refresh4.mjs` (fixture `CORS_MAX_AGE=600`) | exit 0, `logs/refresh4.json` |
| 11 | `node probe-refresh5.mjs` (fixture `CORS_MAX_AGE=600 CORS_NO_STORE=0`) | exit 0, `logs/refresh5.json` |
| 12 | `curl -s -D - -X OPTIONS -H "Origin: http://127.0.0.1:3163" … http://127.0.0.1:8163/api/v1/overview` | 204 with no `Access-Control-Max-Age` (§4.4) |
| 13 | `node probe-cors.mjs` (fixture `CORS_MAX_AGE=600`) | exit 0, `logs/cors.json`; the four-shape table in §4.4 |
| 14 | `node shots.mjs` | exit 0, `logs/shots.log`; 4 PNGs (§ below) |
| 15 | `bash bundle.sh` | exit 0, `logs/bundle.txt` |

Every process started (fixture `python3 server.py`, `next start -p 3163`, headless Chrome) was
stopped by PID by the harness itself (`child.kill("SIGTERM")`, then `SIGKILL` on survivors); no
`pkill -f` and no `killall` was used. No git command beyond `status`/`diff` was run.

Screenshots (AGENTS.md §3a asks for them on UI acceptance; this task changes no UI, so they are
evidence, not an acceptance gate): `/home/efe/Documents/QRadar-Dash-work/ui63/png/heavy-24-1440x900.png`,
`…/heavy-24-1280x800.png`, `…/heavy-24-1024x768.png`, `…/heavy-24-1440x900-bottom.png`. Each shows
24 widgets, 7 chart instances, and no horizontal overflow (`scrollWidth == clientWidth`: 1425/1425,
1265/1265, 1009/1009); page height 5640 px at 1440 and 1280, 7556 px at 1024.

## Open points

1. **F2 needs one more step to be actionable**: a heap-snapshot pair (round trip 1 vs 2) with
   retainer paths, or the same cycles under `--js-flags=--expose-gc` with `queryObjects`, to say
   *what* holds the retained subtree. The probes here localise it (one page tree, first departure,
   client-side only once; unbounded only with full document loads) but not the holder.
2. **The refresh fires two mechanisms, and they do not double the reads.** `AutoRefresh.tsx:118-128`
   calls `router.refresh()` *and* `bump()` in one interval callback; the widgets re-read on the tick
   and the RSC refresh re-renders the tree around them, but `useApiResource`'s shared in-flight map
   merges the two into one request per URL (§4.4: 32 distinct URLs, 34 reads). Whether dropping one
   of the two would change anything visible was not tested — the request counts say it would not,
   and the counts are what this task was asked to measure.
3. **The fixture is not the backend.** All API timings here are a Python fixture answering from
   memory on the same host; the real `/fleet/summary`/`aggregate_metrics` N+1 cost (UR.0.3 item 10,
   B-2.2) is not visible in any number in this report. §2's 424/421 ms cold figures are a *client*
   figure and would not move much with a slower API, because the first widget is a chunk+data race;
   the refresh bytes in §4.4 are all fixture reads. §4.1-§4.4's **request counts** are also inflated
   ~2× by the dev topology F3 describes, and are exact only for a deployment whose API is on a
   different origin from the SPA.
4. **`--no-sandbox`** was required (§ preamble). A stock-SELinux, non-sandboxed analyst host may
   differ in the absolute numbers; the budgets have wide margins (442 ms vs 2000 ms, 86 ms vs
   100 ms, 0 vs 0), so the verdicts should hold.
5. **Refresh = 5 min was not measured.** The task names 60 s at 1 min; the select's other period
   uses the same timer path (`AutoRefresh.tsx:34-37,118-128`), so nothing new was expected, and
   nothing was inferred from it either.
6. **`/fleet/catchall` is a fourth heavy first-load route** (292 kB, §6). It was not one of the
   three the task names, so it is reported and not investigated.
7. **The 24-widget dashboard is 5640 px tall at 1440×900.** Guidelines §9's page-overflow rule is
   about *horizontal* overflow and the page is clean (`scrollWidth == clientWidth` in all three
   viewports, §Gates); the vertical length is inherent to 24 widgets and is what the "scrolled to
   the bottom" measurement was for. Flagging it because §9 and the 24-widget limit interact here.
8. **No budget is given for the load's own request count or bytes** (§4.1), so they are reported
   only. The 165-request / 1.28 MB cold load of the 10-chart dashboard is worth a second look by
   whoever owns UR.8.3's model — 59 scripts is a lot for one page, and F1 is the reason.
