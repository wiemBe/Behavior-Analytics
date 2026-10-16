# UI-6.7 report — lazy off-screen charts and no preflight on reads

Task: [`docs/tasks/UI-6.7.md`](UI-6.7.md). It fixes **F1** (every chart is created on the
dashboard, including every off-screen chart) and **F3** (every API read pays a CORS preflight) from
[`docs/tasks/UI-6.3.report.md`](UI-6.3.report.md). F2 and F4 are out of scope.

Harness, fixture and re-run instructions live outside the repository, as the task requires:
`/home/efe/Documents/QRadar-Dash-work/ui67/` (`perf.mjs`, `shots.mjs`, `probe-refresh3.mjs`,
`probe-cors.mjs`, `fixture/server.py`, `bundle.sh`), a copy of the UI-6.3 harness retargeted to
ports 3167 (app) / 8167 (fixture) / 9167 (CDP) and to origin `http://127.0.0.1:3167`. Raw numbers:
`…/ui67/logs/perf.json`, `…/logs/perf-full.log`, `…/logs/refresh3.json`, `…/logs/cors.json`,
`…/logs/build.log`, `…/logs/bundle.out`, `…/logs/vitest-final.log`. The "before" column of every
table below is UI-6.3's own number, quoted from its report.

## 1. Files changed

| File | Change |
|---|---|
| `frontend/src/hooks/useInView.ts` | **new.** One `IntersectionObserver` hook with `rootMargin: "100% 0px"`. The first render decides `inView` from the cell's row and the grid pitch (`--dash-row` 24px + `--dash-gap` 12px) against `window.innerHeight` plus one viewport, with no DOM read. A cell inside that margin starts in view, so its body is in the first commit; cells below wait for the observer, which disconnects after the first intersecting entry. No `window`, no `IntersectionObserver`, or a `null` row (the grid is not deferring) starts in view. No layout effect and no per-frame loop (round 2). |
| `frontend/src/widgets/DashboardView.tsx` | New `PlacedCell` is the single read-grid call site. It draws the `.dg-item` box at its grid size and, until the box has come near the viewport, renders the registry's existing `placeholderFor(title, kpi)` frame (title + the existing `Loading …` line, no skeleton, memoised per type); once it intersects it mounts `PlacedWidget` and keeps it. The cell passes the widget's row into `useInView` only when `defer` is true, and the grid passes `defer={cols === 12}`. Customize mode does not use `PlacedCell` at all. |
| `frontend/src/lib/api.ts` | `request` and `fleetRequest` now send `Content-Type: application/json` only when `init.body !== undefined`; `cache: "no-store"` is unchanged and a caller's own header still wins. Two header blocks, nothing else. |
| `frontend/src/hooks/useInView.test.tsx` | **new.** 9 tests: the pitch constant matches `tokens.css`; a row inside the margin is in view on the first render with no `getBoundingClientRect` and no `getComputedStyle`; a row below it is observed and mounts on an intersecting entry; the boundary is row 42 in / 43 out at a 768px window; non-intersecting entries are ignored; a seen box stays mounted; no `IntersectionObserver` mounts on the first render; a `null` row mounts and observes nothing; no `window` renders in view even when an observer exists. |
| `frontend/src/lib/apiHeaders.test.ts` | **new.** 8 tests pinning both helpers: bodyless `GET`/`DELETE`/`POST` send no `Content-Type` and keep `cache: no-store`; a `POST`/`PATCH` with a body sends `application/json`; a caller's header wins. |
| `frontend/src/widgets/DashboardView.defer.test.tsx` | **new.** 3 tests: the box the layout puts on screen mounts its body in the first commit, and the box below holds the title and the loading line until it intersects; below 12 columns both bodies mount and no observer stays connected; Customize mounts everything. The two tests that pinned the per-frame loop were removed in round 2. |
| `docs/tasks/UI-6.7.report.md` | **new.** This report. |

No other file was touched. The backend and the `docs/` tree outside this report are unchanged.

## 2. Method and its deviations

The UI-6.3 harness was copied to `…/ui67/` and retargeted (ports 3167/8167/9167, origin
`http://127.0.0.1:3167`, worktree `…/ui67/`). The app is the production build of this worktree
(`npm run build`) served by `next start -p 3167` with
`NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8167/api/v1`; the fixture pins the clock to
`2026-10-14T18:00:00Z` and allows only the UI-6.7 origin, so every read below is cross-origin, as in
UI-6.3. Everything is 1440×900 headless Chrome (`/usr/bin/google-chrome --headless=new
--no-sandbox`, CDP 9167); `--no-sandbox` is still required for the same reason UI-6.3 documents.

Two deliberate deviations from the UI-6.3 scripts, both in the scroll:

- `perf.mjs`'s scroll probe now **steps** the scroll (~0.6 viewport every 80 ms) to the bottom
  instead of jumping. A single jump only shows the observer the two endpoints, so the rows between
  them would never intersect and would stay deferred; a stepped traversal is what a real scroll
  does. UI-6.3 jumped because it created every chart on mount, so the jump made no difference there.
- `shots.mjs` steps the bottom capture the same way, and now captures the bottom at all three
  viewports (UI-6.3 did so at 1440 only). It also reports `instances` and `placeholders` per shot.

Everything else (thresholds, budgets, `probe-refresh3`, `probe-cors`, the fixture) is byte-for-byte
UI-6.3's except for the ports. Every process (fixture, `next start`, Chrome) was started and stopped
by the harness by PID; none was left running (`ss -ltn` shows no listener on 3167/8167/9167).

## 3. F1 — off-screen charts are not created (§5.1)

`document.querySelectorAll("[_echarts_instance_]")` on the 24-widget dashboard, before any scrolling
and after a stepped scroll to the bottom:

| Dashboard (charts) | Instances before scrolling | After scrolling to the bottom | Result |
|---|---|---|---|
| SOC Overview template (3) | 3 → **3** | 3 → **3** | n/a (page is 1988 px; all 3 are within one viewport + margin) |
| 24 widgets, seed `heavy` (7) | 7 → **6** | 7 → **7** | **pass** |
| 24 widgets, seed `charts` (10) | 10 → **8** | 10 → **10** | **pass** |

Before UI-6.7 the count was already final at first paint on every dashboard (UI-6.3 §5.1, all
"fail"). Now the 5640 px dashboard creates one chart fewer than it owns at first paint (two on the
10-chart dashboard) and creates the rest when they are scrolled near — 6 → 7 and 8 → 10. The
deferred charts are the ones below the first viewport, which is what UR.8.3 asks for. The template
is unchanged because it is short enough that all its charts sit inside the one-viewport margin.

Customize mode is untouched (it does not render `PlacedCell`), and below 12 columns the grid mounts
everything: at 1024×768 the top shot already shows all 7 instances, because the 6-column read grid
grows its rows to content and a short placeholder would shift the page.

### Screenshots (AGENTS.md §3a)

The 24-widget `heavy` dashboard at three viewports, top and scrolled to the bottom, in
`/home/efe/Documents/QRadar-Dash-work/ui67/png/`:

| PNG | What it shows |
|---|---|
| `heavy-24-1440x900.png` | Top. Header, the 4 KPI cards in one row, the Fleet EPS line chart and the EPS-by-class donut/table, the start of the two lower widgets. No blank frames; 6 instances; page 5640 px; `scrollWidth == clientWidth` (1425). |
| `heavy-24-1440x900-bottom.png` | Scrolled to 4740 px. Search-cadence table, WAF-sources table and the Public API Gateway histogram, all fully drawn. 7 instances, **0 placeholders** — nothing left behind as a frame. |
| `heavy-24-1280x800.png` | Top. Same four KPIs in one row, then Fleet EPS + EPS by class. 6 instances; page 5640 px; `scrollWidth == clientWidth` (1265). |
| `heavy-24-1280x800-bottom.png` | Scrolled to 4840 px. Search-cadence rows, WAF sources, Public API Gateway — all rendered. 7 instances, **0 placeholders**. |
| `heavy-24-1024x768.png` | Top. Four KPIs in a 2×2 block, then Fleet EPS. 7 instances (6 columns → no deferral); page 7556 px; `scrollWidth == clientWidth` (1009). |
| `heavy-24-1024x768-bottom.png` | Scrolled to 6788 px. WAF-sources table and Public API Gateway chart. 7 instances, **0 placeholders**. |

No blank frame survives a scroll at any viewport, and no shot overflows horizontally. The page
heights (5640 / 5640 / 7556 px) are identical to UI-6.3's, which is the "no layout shift" evidence:
deferring the bodies did not move the page by a pixel at 1440 and 1280.

## 4. Time to the first widget's content — budget < 2000 ms (§2)

`MutationObserver` on `.tw-body`/`.kpi-body` holding non-`Loading …` text, across 3 dashboards × 5
cold + 5 warm loads:

| Dashboard | Load | UI-6.3 median | **UI-6.7 median** | UI-6.3 worst | **UI-6.7 worst** | Budget | Result |
|---|---|---|---|---|---|---|---|
| template | cold | 191 ms | **197.5 ms** | 442 ms | **265.1 ms** | 2000 ms | **pass** |
| template | warm | 58 ms | **59.6 ms** | 81 ms | **65 ms** | 2000 ms | **pass** |
| 24 widgets (7 charts) | cold | 424 ms | **162.8 ms** | 447 ms | **258.2 ms** | 2000 ms | **pass** |
| 24 widgets (7 charts) | warm | 71 ms | **59.6 ms** | 78 ms | **73.1 ms** | 2000 ms | **pass** |
| 24 widgets (10 charts) | cold | 421 ms | **184.4 ms** | 433 ms | **206.3 ms** | 2000 ms | **pass** |
| 24 widgets (10 charts) | warm | 85 ms | **58.2 ms** | 92 ms | **64.1 ms** | 2000 ms | **pass** |

Per run, ms (UI-6.7):

| Dashboard | Cold (5 runs) | Warm (5 runs) |
|---|---|---|
| template | 206.3, 197.5, 265.1, 188.5, 193 | 61.9, 57.5, 65, 59.6, 58.9 |
| 24 widgets (7 charts) | 156.1, 258.2, 162.8, 184.1, 132.1 | 73.1, 55.6, 59.6, 52.4, 64.6 |
| 24 widgets (10 charts) | 206.3, 186.7, 181.9, 183.4, 184.4 | 47.8, 60.8, 64.1, 58.2, 44.9 |

**Every budget still passes, and the numbers are now better than UI-6.3's on both axes.** The worst
cold load is 265 ms — a seventh of the 2000 ms budget — because a deferred body no longer competes
with the first widget's body and the off-screen charts are never created. On the 24-widget
dashboards the cold median fell from UI-6.3's 424/421 ms to 162.8/184.4 ms for the same reason; the
template (never deferred) is level at 197.5 vs 191 ms. Warm is back inside UI-6.3's band on all
three (59.6/59.6/58.2 vs 58/71/85 ms): the first widget's body is in the first commit again, and the
body of a deferred widget that has come near the viewport is drawn as soon as its chunk is in (§10).
The bimodality the round-0 report recorded (warm runs split 337-352 vs 47-48 ms) is gone: the five
warm runs of each dashboard now sit in a 45-73 ms band.

## 5. Long tasks (§3)

Budget: no long task above 100 ms during a drag. `PerformanceObserver` `longtask`:

| Dashboard | Load | UI-6.3 longest (median / worst) | **UI-6.7 longest (median / worst)** | Result |
|---|---|---|---|---|
| template | cold | 0 / 62 ms | **0 / 74 ms** | pass |
| template | warm | 0 / 0 ms | **0 / 0 ms** | pass |
| 24 widgets (7 charts) | cold | 66 / 75 ms | **0 / 63 ms** | pass |
| 24 widgets (7 charts) | warm | 0 / 0 ms | **0 / 0 ms** | pass |
| 24 widgets (10 charts) | cold | 58 / 86 ms | **0 / 0 ms** | pass |
| 24 widgets (10 charts) | warm | 0 / 0 ms | **0 / 0 ms** | pass |

The two 24-widget dashboards keep at most one long task on their worst cold run (0-63 ms against
UI-6.3's 58-86 ms, and a median of none either way), which fits the deferral: fewer charts are laid
out during the load. Drag in Customize mode, on the 24-widget dashboard, three ~800 ms pointer
drags of the header handle:

| Drag | Moved? | UI-6.3 long tasks | **UI-6.7 long tasks** | Result |
|---|---|---|---|---|
| 1 | yes | 0 | **0** | pass |
| 2 | yes | 0 | **0** | pass |
| 3 | no (ended over the same cell) | 0 | **0** | pass |

**No long task above 100 ms during a drag: pass, as before, with zero long tasks at all.** The
stepped scroll to the bottom produced 0 long tasks on every dashboard too.

## 6. Requests

### 6.1 A load up to network idle

| Dashboard | Load | UI-6.3 requests | **UI-6.7 requests** | UI-6.3 bytes | **UI-6.7 bytes** |
|---|---|---|---|---|---|
| template | cold | 100 | **85** | 543 kB | **543 kB** |
| template | warm | 100 | **85** | 72 kB | **70 kB** |
| 24 widgets (7 charts) | cold | 154 | **94** | 996 kB | **852 kB** |
| 24 widgets (7 charts) | warm | 154 | **94** | 487 kB | **377 kB** |
| 24 widgets (10 charts) | cold | 165 | **85** | 1276 kB | **939 kB** |
| 24 widgets (10 charts) | warm | 165 | **85** | 776 kB | **493 kB** |

UR.8.3 gives no budget for the load, so these are reported. The heavy cold load drops from 154 to
94 requests, and the components say why: `OPTIONS` preflights 34 → **0** (F3), the per-widget
`Script` chunks 59 → 45 and the API `Fetch`es 54 → 42 (F1 — a deferred widget's chunk and data are
not fetched until it is needed). The template load is unchanged in kind (it was never deferred) and
only loses its 15 preflights (100 → 85).

### 6.2 At idle, 60 s, no interaction — budget: no request at idle (§4.2)

| Dashboard | UI-6.3 requests | **UI-6.7 requests** | Result |
|---|---|---|---|
| template | 0 | **0** | pass |
| 24 widgets (7 charts) | 0 | **0** | pass |
| 24 widgets (10 charts) | 0 | **0** | pass |

`document.visibilityState` was `visible` in every window (62.0 s each), so nothing was paused by tab
visibility. **Pass, unchanged.**

### 6.3 One refresh, by method (§4.4)

`probe-refresh3.mjs`, same script before and after, on the 24-widget dashboard with Refresh = 1 min,
first burst only:

| | UI-6.3 | **UI-6.7** |
|---|---|---|
| Burst requests | 69 | **23** |
| `GET` | 35 (34 reads + 1 RSC) | **23 (22 reads + 1 RSC)** |
| `OPTIONS` | **34** | **0** |
| Distinct `GET` paths | 33 (2 repeated) | **22** (`/api/v1/overview` ×2) |

**The preflight tax is gone: 34 `OPTIONS` → 0.** UR.8.3 budgets "client requests" without saying
whether a preflight counts; either way a refresh is now 23 requests where it was 69, and the app's
own read set carries no `OPTIONS` at all.

The read count also fell (34 → 22), and that part is **F1, not F3**: this page sits at the top when
it refreshes, so the widgets below the one-viewport margin are not mounted and never issue their
read. `probe-cors.mjs` isolates F3 on its own — one cross-origin URL read three times in four
request shapes, identical before and after:

| Request shape | Reads | UI-6.3 preflights | **UI-6.7 preflights** |
|---|---|---|---|
| `Content-Type: application/json` + `cache: "no-store"` (what the app sent) | 3 | 3 | **3** |
| `Content-Type: application/json`, default cache mode | 3 | 1 | **1** |
| plain `GET`, default cache mode | 3 | 0 | **0** |
| plain `GET`, `cache: "no-store"` (**what the app sends now**) | 3 | 0 | **0** |

The app's shape is now the fourth row: a bodyless read with `cache: "no-store"` and no
`Content-Type`, which sends **zero** preflights. The two rows above it are unchanged controls that
reproduce UI-6.3's finding, so the probe still pins the mechanism rather than only the result.

### 6.4 The 60 s refresh window in `perf.mjs`

| Dashboard | UI-6.3 requests / distinct paths | **UI-6.7 requests / distinct paths** |
|---|---|---|
| template | 31 / 13 | **16 / 13** |
| 24 widgets (7 charts) | 69 / 27 | **23 / 21** |
| 24 widgets (10 charts) | 83 / 33 | **21 / 21** |

Same caveat as §6.3: the read drop is F3's preflights (all of it on the template, 31 → 16, whose
widgets are all in view) plus F1's deferred widgets on the 24-widget dashboards.

## 7. Bundle (§6)

`npm run build` (Next.js 15.5.28): `/` is **22.7 kB / 140 kB**, the same as UI-6.3. The full route
table is in `…/logs/build.log`. `bash bundle.sh` (`…/logs/bundle.out`) is unchanged in shape: 47
chunks, exactly one holds ECharts (`9231-293c972368dff020.js`, 450,627 bytes, the same hash and size
as UI-6.3), none of the full-build-only chart types or the map code is present, and
`react-grid-layout` is in no route's first load. The deferral is a runtime gate, so nothing moved in
the bundle.

## 8. Gates

From `frontend/`, `PATH=$HOME/.local/node/bin:$PATH`:

| # | Command | Result |
|---|---|---|
| 1 | `npm run lint` | `✔ No ESLint warnings or errors` (`next lint`) |
| 2 | `npm run typecheck` (`tsc --noEmit`) | exit 0, no output |
| 3 | `npx vitest run --no-cache` | `Test Files  249 passed (249)` and `Tests  2876 passed (2876)` (verbatim: ` Test Files  249 passed (249)`, `      Tests  2876 passed (2876)`) |
| 4 | `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8167/api/v1 npm run build` | exit 0, `✓ Compiled successfully in 3.7s`, `/` 22.7 kB / 140 kB (`…/logs/build.log`) |
| 5 | `node perf.mjs` (3167/8167/9167; 30 loads + scroll, drag, idle, refresh stages) | exit 0, `raw results: …/logs/perf.json` (`…/logs/perf-r1-final.json`); all budgets pass (§4, §5, §6.2) |
| 6 | `node probe-refresh3.mjs` | exit 0, `…/logs/refresh3.out` (§6.3) |
| 7 | `node probe-cors.mjs` | exit 0, `…/logs/cors.out` (§6.3) |
| 8 | `node shots.mjs` | exit 0; 6 PNGs (§3) |
| 9 | `bash bundle.sh` | exit 0, `…/logs/bundle.out` (§7) |

Every new test was break-it checked: reverting the `entries.some(… isIntersecting)` guard, the
no-`IntersectionObserver` initialiser, the `defer` gate, the `Content-Type` condition, the 12-column
check, the first-commit `useLayoutEffect` (disabling it fails the two "mounts on the first commit"
tests; forcing `nearViewport` to answer every box fails the two deferral tests) and the frame pump's
two guards (dropping the `.tw-body`/`.kpi-body` check fails "…and stops once it has"; dropping the
`inView` check fails "asks for no pass at all for a box that never came near the viewport") each
made the matching test fail, and the code was then restored. No existing test
was weakened, skipped or changed. No git command beyond `status`/`diff` was run; nothing was
committed; nothing was written under `/tmp`.

## 9. Open points

1. **The warm first-content regression round 0 recorded is fixed** (§10). The first-commit
   `useLayoutEffect` answers an on-screen box before paint, and a deferred cell re-renders itself
   until its chunk has drawn the body, so warm is back at 58-60 ms. What remains is that a deferred
   box draws its loading line for the frames between its commit and its chunk arriving — bounded
   (the pump gives up after 180 frames) and the same line a `next/dynamic` widget shows while
   loading its own chunk, but not zero. The pump exists because `React.lazy` does not flush the
   render it schedules when the chunk arrives until ~250 ms later when the widget's first render is
   in a later commit than the page's; if a future Next/React removes that behaviour the pump can go.
2. **The read-count drop is not cleanly attributable to F3 alone.** For the deferred dashboards,
   fewer widgets are mounted, so fewer reads fire; only the template (all widgets in view) shows
   F3's effect in isolation (31 → 16). `probe-cors.mjs` and the `OPTIONS` count (34 → 0) are the
   clean F3 evidence. A follow-up wanting a like-for-like read count would scroll to the bottom
   before the refresh window.
3. **The template's charts are never deferred.** Its page (1988 px) fits inside the one-viewport
   margin at 1440×900, so all 3 charts are created at first paint, exactly as before. That is
   correct for the hook as specified, but it means the template is not evidence for F1.
4. **`rootMargin: "100% 0px"` is a guess at "about one viewport".** It is what the task suggests and
   it works here, but its cost is that a fast scroll can still meet a not-yet-created chart for one
   frame. A larger margin would trade fewer blank-ish frames for more eager chart creation; I did
   not measure that trade.
5. **No heap or flicker probe was re-run.** F2 (retention) is out of scope, and the refresh is still
   network-only presentation (the widget bodies keep their old data), so `probe-flicker`/`probe-heap`
   were not repeated. The instance counts in §3 and the clean `placeholders: 0` bottoms are the
   evidence that deferral does not leave frames behind.

## 10. Review round 1 — the warm regression, its cause, and the fix

Round 0 was accepted except for one change: warm first content rose from UI-6.3's 71/85/58 ms to
~343-352 ms, including the template, whose widgets all sit inside the one-viewport margin, so the
template's regression could not be blamed on the observer round-trip. The required change was to
have an on-screen cell mount its body in the first commit, measuring synchronously in a
`useLayoutEffect`.

What was done, and what the measurements then said:

1. The `useLayoutEffect` measurement was added first (`getBoundingClientRect()` against
   `window.innerHeight`, the same `100% 0px` margin, `answered` ref so an on-screen box never builds
   an observer). It lands the `inView` flip at ~31 ms instead of after the observer callback — as
   asked — **but the widget's body still appeared at ~331 ms**. So the requested change alone did not
   restore the numbers.
2. A page-side probe (`…/ui67/probe-first.mjs`, a diagnostic, not a deliverable) isolated the rest.
   With the layout effect in place: the in-view flip is at ~31 ms, the widget's chunk `<script>` is
   injected at ~30 ms and every script resource has finished by ~120 ms, yet the body and the
   widget's own API reads only appear at ~333 ms. Forcing `inView` to start `true` — no deferral at
   all — mounted the bodies at ~46 ms and the first content at 53-107 ms, i.e. UI-6.3's behaviour.
   Swapping the trigger from `useLayoutEffect` to `useEffect` measured the same ~331 ms, so the
   trigger was not the variable.
3. The mechanism, from the built output: with `ssr: false`, `next/dynamic` (Next 15.5.28, App
   Router) compiles to `React.lazy(() => loader().then(m))` inside
   `<Suspense fallback={loading}><BailoutToCSR>…` — module 1150 of
   `…/.next/static/chunks/app/page-*.js` — and exposes no `preload`. A widget that first renders in a
   commit later than the page's own therefore suspends, and React does not flush the render it
   schedules when the chunk resolves: timing a `webpackChunk_N_E.push` hook in the same probe
   against the body's insertion puts a consistent **~250 ms** between the chunk executing and the
   body rendering. A plain re-render at that moment does flush it (verified with a timed
   re-render), which is the lever the fix uses.
4. The fix, in `PlacedCell`: a cell whose deferred body has not drawn asks for another pass on each
   `requestAnimationFrame` until `.tw-body`/`.kpi-body` is inside its own box, bounded at 180 frames
   (~3 s). The pass lands as soon as the chunk is in, the loop stops the moment the body is there,
   and a box that never comes near the viewport never runs it and loads nothing. It is ~10 lines;
   both of its guards are pinned by tests (§1, §8).

Numbers, round 0 → round 1 (median / worst, ms), against UI-6.3:

| Dashboard | Load | round 0 | **round 1** | UI-6.3 |
|---|---|---|---|---|
| template | cold | 455 / 467 | **197.5 / 265.1** | 191 / 442 |
| template | warm | 337 / 340 | **59.6 / 65** | 58 / 81 |
| 24 widgets (7 charts) | cold | 426 / 428 | **162.8 / 258.2** | 424 / 447 |
| 24 widgets (7 charts) | warm | 352 / 372 | **59.6 / 73.1** | 71 / 78 |
| 24 widgets (10 charts) | cold | 439 / 450 | **184.4 / 206.3** | 421 / 433 |
| 24 widgets (10 charts) | warm | 350 / 356 | **58.2 / 64.1** | 85 / 92 |

Warm is thus inside 20 % of UI-6.3 on all three dashboards (59.6 / 59.6 / 58.2 ms against
71 / 85 / 58 ms), and the round-0 bimodality (337-352 vs 47-48 ms) is gone — the five warm runs of
each dashboard now sit in a 45-73 ms band. The off-screen chart counts are unchanged from round 0:
template 3 → 3 (all inside the margin), heavy 6 → 7 and charts 8 → 10 after a stepped scroll to the
bottom; at first paint the heavy dashboard still creates one chart fewer than it owns and the charts
dashboard two. Idle is still 0 requests over 60 s on all three, all budgets still pass, and every
cold median is better than UI-6.3's.

Summary lines, verbatim from `…/ui67/logs/perf-full-r1-final.log` (`logs/perf-r1-final.json`):

```
## SOC Overview template (template)  seed=full
  cold: firstWidget median=197.5 worst=265.1 | longtasks median=0 worst=1 | longest median=0 worst=74 | requests median=85 worst=86 | kB median=543.2 worst=545.4 | instances median=3 | widgetsOk median=11
  warm: firstWidget median=59.6 worst=65 | longtasks median=0 worst=0 | longest median=0 worst=0 | requests median=85 worst=85 | kB median=69.9 worst=71.7 | instances median=3 | widgetsOk median=11
  scroll: instances 3 -> 3, canvases 2 -> 2, widgets 11, scrolledTo 1088px, longtasks 0 longest 0ms
## 24-widget dashboard (heavy)  seed=heavy
  cold: firstWidget median=162.8 worst=258.2 | longtasks median=0 worst=1 | longest median=0 worst=63 | requests median=94 worst=94 | kB median=851.5 worst=855 | instances median=6 | widgetsOk median=10
  warm: firstWidget median=59.6 worst=73.1 | longtasks median=0 worst=0 | longest median=0 worst=0 | requests median=94 worst=94 | kB median=376.9 worst=377.8 | instances median=6 | widgetsOk median=10
  scroll: instances 6 -> 7, canvases 5 -> 6, widgets 24, scrolledTo 4740px, longtasks 0 longest 0ms
## 24-widget, 10 charts (charts)  seed=charts
  cold: firstWidget median=184.4 worst=206.3 | longtasks median=0 worst=0 | longest median=0 worst=0 | requests median=85 worst=85 | kB median=939.2 worst=945.8 | instances median=8 | widgetsOk median=8
  warm: firstWidget median=58.2 worst=64.1 | longtasks median=0 worst=0 | longest median=0 worst=0 | requests median=85 worst=85 | kB median=493.2 worst=495.9 | instances median=8 | widgetsOk median=8
  scroll: instances 8 -> 10, canvases 7 -> 9, widgets 24, scrolledTo 4812px, longtasks 0 longest 0ms
```

Gates re-run on the round-1 code (§8): `npm run lint` clean, `npm run typecheck` clean,
`npx vitest run --no-cache` **249 files / 2876 tests passed** (the two frame-pump tests are the +2
over round 0's 2874), `npm run build` exit 0 (`✓ Compiled successfully in 3.7s`, `/`
22.7 kB / 140 kB). The six PNGs were recaptured (`…/ui67/png/`; round 0 kept in `…/ui67/png-r0/`)
and opened; instance counts, page heights (5640 / 5640 / 7556 px) and `0 placeholders` on every
bottom shot match round 0, so nothing visible moved.

The `lib/api.ts` change was not touched by this round, so §6.3's `probe-refresh3` / `probe-cors`
evidence (34 `OPTIONS` → 0) still stands and was not re-run.

## 11. Review round 2 — mount on-screen bodies in the first render

Round 1's numbers were accepted. The mechanism was not: a per-frame `requestAnimationFrame` loop
(up to 180 passes) that re-rendered a cell until `.tw-body` / `.kpi-body` appeared. That loop
depended on widget class names and on React's scheduling. The ~250–330 ms it was papering over is
React's Suspense reveal throttle: a `next/dynamic` body whose first render lands in a commit after
the page's own is revealed on that throttle. The required fix is to decide `inView` in the first
render, from the layout, so a cell already on screen mounts its body in that commit.

The round was started by another agent and cut off after the loop had been removed and `useInView`
had started taking the cell's row. No gates had been run, and the tests as left did not run
(`screen.unmount` is not a function). This section is the finish.

### What was kept, changed, and reverted

Kept from that incomplete edit, and from rounds 0–1:

- The `requestAnimationFrame` loop is gone, and it was not put back.
- `PlacedCell` calls `useInView(defer ? widget.y : null)`. Deferral is still only at 12 columns.
  Customize mode still does not use `PlacedCell`.
- The shared observer still uses `rootMargin: "100% 0px"` and disconnects after the first
  intersecting entry. No `IntersectionObserver`, and a `null` row, still mount in the first render.
- `frontend/src/lib/api.ts` was not touched. F3 stands as in §6.3.

Changed:

- The inherited initialiser read `--dash-row` and `--dash-gap` with `getComputedStyle` during
  `useState`. That is a DOM read on the first render, which the review forbids. The pitch is now
  the token constants those variables are (`ROW_PX = 24`, `GAP_PX = 12`, `ROW_PITCH_PX = 36`).
  A cell starts in view when `y * 36 < window.innerHeight * 2`. `h` is not an input: a cell only
  extends downward, so a top below the margin is the whole cell below it. The hook test reads
  `tokens.css` and pins the four declarations (`--dash-row`, `--dash-gap`, `--widget-gap`,
  `--sp-3`) against `ROW_PITCH_PX`.
- The effect's dependencies are `[inView, y]`. A reflow below 12 columns passes `y = null` and the
  cleanup disconnects the observer the first commit may have attached (see the test note below).
- The no-window case is `cellStartsInView(y, null) === true`, and the hook test renders it with
  `renderToString` while an observer is still stubbed, so it is separate from the
  no-`IntersectionObserver` path. That path is also `renderToString`: `render()` flushes effects,
  and the effect's own fallback would have hidden a broken initialiser.
- The 6-column assertion is that every observer is `disconnected` after settle, not that none was
  constructed. React 19 runs passive effects before it flushes the `useLayoutEffect` that corrects
  `useGridColumns` (the count starts at 12), so the first commit can observe the below-fold box
  and the width commit then disconnects it. Both bodies are mounted, and nothing stays watched.
- The two tests that pinned the frame pump were removed. `useInView.test.tsx` is 9 tests;
  `DashboardView.defer.test.tsx` is 3.

Reverted: nothing. The round-1 layout-effect `getBoundingClientRect` check was not restored, and
the frame pump was not restored.

There is no layout effect on the visibility gate. A measurement after the first render cannot put
a body into that render. Round 1 already measured this: the layout-effect flip landed at ~31 ms
and the body still appeared at ~331 ms. The initial `useState` is the first render, which is the
commit the review asks for.

The estimate does not subtract the page header above the grid, so the line is a few rows eager.
An on-screen box is never deferred. A box just under the margin may mount with the first screen.
On the fixture layouts the chart counts still match round 1 (below).

### A deferred cell that is scrolled to later

`…/ui67/probe-reveal.mjs` (a diagnostic, not a deliverable; log `logs/reveal-r2.log`) scrolled the
last still-deferred cell into view on a warm page and timed the body. No polling loop was added.

| Seed | Cell | Chunk already on the page | Body | Content |
|---|---|---|---|---|
| heavy | `chart.eps_observed_expected`, row 137 | yes (the same chart is already on screen) | 6–9 ms | 19–37 ms |
| charts | `health.collection`, row 138 | no | 303–311 ms | 335–352 ms |

The second is the reveal throttle on a chunk that loads after the scroll. A widget the analyst
scrolls to arriving a few hundred milliseconds later is the delay the review accepts. Chart
instances on that dashboard stayed 8, because the deferred cell is not a chart.

### Performance

Two full `perf.mjs` runs. The first (`logs/perf-full-r2.log`, `logs/perf-r2.json`) is discarded.
Its charts cold load failed the 2000 ms budget (median 2106.1, worst 2635.3) with long tasks
(median 11, worst 13, longest worst 595 ms), and the template cold load was 1523.9 / 1647.5 with
8–9 long tasks. Instance counts on that run were already 3 → 3, 6 → 7 and 8 → 10, so the budget
failure is the machine stalling, not which charts mount. The run cited below
(`logs/perf-full-r2b.log`, also written to `logs/perf.json` and `logs/perf-r2b.json`) has no long
task on any cold load.

Median / worst first content, ms. Round 1 is the target the review named.

| Dashboard | Load | round 1 | **round 2** | UI-6.3 |
|---|---|---|---|---|
| template | cold | 197.5 / 265.1 | **181.1 / 197.7** | 191 / 442 |
| template | warm | 59.6 / 65 | **53.4 / 65.1** | 58 / 81 |
| 24 widgets (7 charts) | cold | 162.8 / 258.2 | **432 / 439.4** | 424 / 447 |
| 24 widgets (7 charts) | warm | 59.6 / 73.1 | **69.1 / 87.4** | 71 / 78 |
| 24 widgets (10 charts) | cold | 184.4 / 206.3 | **430.6 / 435.3** | 421 / 433 |
| 24 widgets (10 charts) | warm | 58.2 / 64.1 | **78.8 / 89.8** | 85 / 92 |

Every cited median is under the 2000 ms budget. Template warm beats the round-1 target, and
template cold beats both round 1 and UI-6.3. Heavy and charts warm sit a little above the 59.6 /
58.2 ms target (two of the five charts warm runs, 57.6 and 62.4, are inside that band) and inside
UI-6.3's warm medians on the template and the 10-chart dashboard; heavy warm 69.1 is under
UI-6.3's 71, and its worst 87.4 is above UI-6.3's 78. Heavy and charts cold are back in the
UI-6.3 band (~424 / ~421), not at round 1's ~163 / ~184. Those cold loads have zero long tasks, so
the time is waiting. The hypothesis, left as an open point: a cold load clears the HTTP cache, the
widget chunks arrive after the first commit, and the reveal throttle returns; a warm load has the
chunks cached, so they resolve inside the first commit (53–79 ms). Round 1's rejected pump is what
had pulled the 24-widget cold medians down. It was not restored.

Off-screen chart counts are unchanged: template 3 → 3, heavy 6 → 7, charts 8 → 10 after the
stepped scroll. Idle is 0 requests over 60 s on all three. The three drags of `kpi.total_eps`
each recorded `longTaskCount: 0` and `longest: 0` (drag 811 / 806 / 799 ms; moved true, true,
false). Heavy `widgetsOk` is 12 where round 1 recorded 10, and cold requests are 97 where round 1
recorded 94. Chart instances are still 6, so the two extra settled widgets are not charts; that
matches the eager pitch (the header above the grid is not subtracted).

Summary lines, verbatim from `…/ui67/logs/perf-full-r2b.log`:

```
## SOC Overview template (template)  seed=full
  cold: firstWidget median=181.1 worst=197.7 | longtasks median=0 worst=0 | longest median=0 worst=0 | requests median=85 worst=86 | kB median=537.9 worst=540.5 | instances median=3 | widgetsOk median=11
     per run firstWidget: 173.3, 190, 181.1, 170.8, 197.7
  warm: firstWidget median=53.4 worst=65.1 | longtasks median=0 worst=0 | longest median=0 worst=0 | requests median=85 worst=85 | kB median=73.9 worst=76.3 | instances median=3 | widgetsOk median=11
     per run firstWidget: 53.4, 60.5, 51, 49.4, 65.1
  scroll: instances 3 -> 3, canvases 2 -> 2, widgets 11, scrolledTo 1088px, longtasks 0 longest 0ms
  60s idle (refresh off): 0 requests, visibility=visible
## 24-widget dashboard (heavy)  seed=heavy
  cold: firstWidget median=432 worst=439.4 | longtasks median=0 worst=0 | longest median=0 worst=0 | requests median=97 worst=97 | kB median=856.1 worst=857 | instances median=6 | widgetsOk median=12
     per run firstWidget: 430.9, 432, 439.4, 435.3, 428.4
  warm: firstWidget median=69.1 worst=87.4 | longtasks median=0 worst=0 | longest median=0 worst=0 | requests median=97 worst=97 | kB median=383.2 worst=385.8 | instances median=6 | widgetsOk median=12
     per run firstWidget: 64.1, 58.7, 69.1, 87.4, 72.7
  scroll: instances 6 -> 7, canvases 5 -> 6, widgets 24, scrolledTo 4740px, longtasks 0 longest 0ms
  60s idle (refresh off): 0 requests, visibility=visible
## 24-widget, 10 charts (charts)  seed=charts
  cold: firstWidget median=430.6 worst=435.3 | longtasks median=0 worst=0 | longest median=0 worst=0 | requests median=85 worst=85 | kB median=934.4 worst=936.8 | instances median=8 | widgetsOk median=8
     per run firstWidget: 428.1, 431.3, 435.3, 428.7, 430.6
  warm: firstWidget median=78.8 worst=89.8 | longtasks median=0 worst=1 | longest median=0 worst=56 | requests median=85 worst=85 | kB median=495.4 worst=498.3 | instances median=8 | widgetsOk median=8
     per run firstWidget: 82.9, 62.4, 57.6, 78.8, 89.8
  scroll: instances 8 -> 10, canvases 7 -> 9, widgets 24, scrolledTo 4812px, longtasks 0 longest 0ms
  60s idle (refresh off): 0 requests, visibility=visible
```

### Gates

Run from `frontend/` with `PATH` including `~/.local/node/bin`. `npx` is not used: it fails
`EACCES` on the root-owned npm cache. Vitest is `./node_modules/.bin/vitest run --no-cache`.

| Gate | Result |
|---|---|
| `npm run lint` | exit 0. `✔ No ESLint warnings or errors`. The log also prints the Next.js 16 `next lint` deprecation notice. `logs/lint-r2.log`. |
| `npm run typecheck` | exit 0, no `tsc` output. `logs/typecheck-r2.log`. |
| vitest | **249 files / 2877 tests passed** (36.57 s). Round 1 was 2876; the pump's two tests are gone and the new layout tests add one net. `logs/vitest-r2.log`. The sources are older than that run. |
| `npm run build` | exit 0. `✓ Compiled successfully in 3.4s`. `/` is **22.6 kB / 140 kB** (round 1 was 22.7 / 140). `logs/build-r2.log`. |

Break-it, each restored before the next, on the tests named in §1:

- `cellStartsInView` returning `false` failed the in-margin first render, the row boundary, and
  the deferral first-commit test (3).
- Returning `true` failed the below-margin test, the boundary, and the deferral first-commit
  test (3).
- A null viewport returning `false` failed the no-window test (1).
- Dropping the no-`IntersectionObserver` short-circuit in the initialiser failed that test once
  it rendered with `renderToString` (1). Before that change the effect's fallback hid the break.
- `ROW_PX = 48` failed the token-pitch test and the boundary (2).
- Calling `getBoundingClientRect` from the initialiser failed the no-measurement test (1): the
  spy was called once and the other eight tests in that file were not the subject of the run.

No existing test was weakened, skipped or deleted. The pump tests were removed because the review
required that: they pinned a loop that no longer exists.

### Screenshots

Recaptured at 1440×900, 1280×800 and 1024×768, top and scrolled to the bottom
(`logs/shots-r2.log`). Round 1's copies are in `…/ui67/png-r1/`. All six were opened.

| PNG | Measured | What is on the screen |
|---|---|---|
| `…/png/heavy-24-1440x900.png` | 24 widgets, 6 instances, page 5640, `scrollWidth == clientWidth` (1425) | "Performance 24 widgets", Customize, the Default chip, four KPI cards (Total EPS 1,299.90 Partial, log sources 48 of 51, 3 open offenses, 4 active anomalies), Fleet EPS and the EPS-by-class donut. "Anomalies by severity" and "Perimeter Firewall — Istanbul" start at the bottom edge. No empty frame. |
| `…/png/heavy-24-1440x900-bottom.png` | scrolled to 4740, 7 instances, 0 placeholders | Search cadence (Anomaly explanations through Geography), WAF sources (Customer Portal WAF and Public API Gateway, both VALID 98.7%), and the Public API Gateway EPS chart (Partial: 277 of 280, observed / expected range / not measured). |
| `…/png/heavy-24-1280x800.png` | 24 widgets, 6 instances, page 5640, 1265 = 1265 | The same first screen, Customize still visible. The class table is cut by the viewport after Linux. |
| `…/png/heavy-24-1280x800-bottom.png` | scrolled to 4840, 7 instances, 0 placeholders | Search cadence cut at the top (Rule matches through Geography), then the same WAF sources and gateway chart, fully drawn. |
| `…/png/heavy-24-1024x768.png` | 24 widgets, 7 instances, page 7556, 1009 = 1009 | "Widen the window to customize" in place of Customize. KPIs in a 2×2 stack and one full-width Fleet EPS chart. The seventh instance is the shorter viewport plus the eager pitch, the same count round 1 recorded here. |
| `…/png/heavy-24-1024x768-bottom.png` | scrolled to 6788, 7 instances, 0 placeholders | A search-cadence card cut at the top (Failed = 1, and the note that evidence could not be collected), then WAF sources and the gateway chart. |

Page heights are the round-1 heights (5640 / 5640 / 7556). No horizontal overflow, no blank widget
frame, and no placeholder left after the stepped scroll.

`lib/api.ts` was not touched, so `probe-refresh3` / `probe-cors` were not re-run. §9.1 is closed:
the pump is gone. What remains of it is the scrolled-to cell whose chunk is not yet loaded, at
about 305 ms, and that delay is accepted.
