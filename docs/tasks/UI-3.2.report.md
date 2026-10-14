# UI-3.2 report — dashboard drag, resize and keyboard arrangement

## Files changed

- `frontend/package.json` — pins `react-grid-layout` at exactly `2.2.4`.
- `frontend/package-lock.json` — records the dependency and its transitive packages.
- `frontend/src/app/tokens.css` — adds the dashboard arrangement, resize-handle and stacking tokens.
- `frontend/src/components/home/WidgetFrame.tsx` — exposes the existing title row as the drag
  handle and accepts the compact Arrange disclosure through the edit context. This is the only
  frame hook needed; it avoids duplicating or repositioning widget headings.
- `frontend/src/widgets/Grid.tsx` — adds the dynamically loaded 12-column React Grid Layout editor,
  pointer drag/resize configuration, data geometry, and named keyboard steppers.
- `frontend/src/widgets/Grid.test.tsx` — covers the stepper names, activation, and disabled bounds.
- `frontend/src/widgets/DashboardView.tsx` — selects the editor only in 12-column customize mode,
  retains the server-rendered CSS grid for reads, compacts removal, and keeps the edited layout in
  session memory after Done.
- `frontend/src/widgets/DashboardView.test.tsx` — covers deferred editor loading, read/narrow-mode
  exclusion, geometry attributes, and persistence after Done.
- `frontend/src/widgets/dashboard.css` — contains only the needed React Grid Layout positioning,
  placeholder and south-east resize rules plus the compact, overlaid Arrange panel.
- `frontend/src/widgets/layout.ts` — adds pure vertical compaction, one-cell move, and one-cell
  resize operations with registry and 12-column bounds.
- `frontend/src/widgets/layout.test.ts` — covers every direction, bounds, collisions, compaction,
  and the `x + w <= 12` invariant.
- `docs/tasks/UI-3.2.report.md` — this evidence and hand-off report.

Outside the repository, as required by the amendment:

- `/home/efe/Documents/QRadar-Dash-work/ui32/fixture/server.py` — self-contained deterministic API
  with a `2026-10-14T18:00:00Z` clock and the real Home, catalog-widget and top-bar response shapes.
- `/home/efe/Documents/QRadar-Dash-work/ui32/fixture/README.md` — fixture startup and scope notes.
- `/home/efe/Documents/QRadar-Dash-work/ui32/verify.mjs` — CDP pointer, keyboard, geometry,
  hydration, network, overflow and screenshot verification.
- `/home/efe/Documents/QRadar-Dash-work/ui32/logs/verify-output.txt` — exact successful script output.
- `/home/efe/Documents/QRadar-Dash-work/ui32/png/*.png` — the six required captures.

## What I did

`react-grid-layout@2.2.4` passed the acceptance checks with React 19, so no fallback was needed. The
read view is still the original CSS grid. The editor is a `next/dynamic` client island with
`ssr: false` and is requested only after Customize opens at 12 columns. It uses
`useContainerWidth({ measureBeforeMount: true })`, reads `--dash-row` and `--dash-gap` once, uses
vertical compaction, and takes every item's min/max size from the registry.

The title row is the pointer drag handle. Interactive elements are excluded from drag starts, and
only the south-east resize handle is enabled. React Grid Layout's transforms are the only new
inline positioning mechanism. Read-mode placement continues to use UI-3.1's accepted custom
properties.

For keyboard access, each title row has an Arrange disclosure containing four Move and four Resize
buttons. Their accessible names include the widget title, and unavailable directions are disabled.
The panel overlays the widget body instead of consuming grid height, so opening it cannot squeeze
the content at template sizes. Both pointer and keyboard paths update the same layout-v1 state;
Done removes edit chrome but retains the geometry in memory. Nothing is persisted, and the
session-only statement remains visible in customize mode.

The hand-written grid CSS has no library stylesheet, animation, gradient, glow, or decorative
shadow. The placeholder, resize mark, focus treatment and stacking all use existing or newly named
tokens.

The reusable fixture covers `/health/ready`, `/me`, `/overview`, search cadence, fleet summary and
EPS series, source behavior, anomaly summary/list, offenses and analytics, coverage, rule health,
catch-all senders, WAF source status, plus the top-search `/fleet/sources` and `/rules` reads. A
direct fixture smoke check returned HTTP 200-shaped results for both added top-search routes.

### Existing-test edits

- `DashboardView.test.tsx`: existing edit-mode assertions now wait for the dynamic client editor
  and use its exposed data geometry. This is necessary because the editor no longer exists in the
  initial read bundle. The file also gains the required narrow/read exclusion and Done-retention
  coverage.
- `layout.test.ts`: the existing layout suite is extended around the new pure move, resize and
  compaction functions; no existing expectation was weakened or removed.

## Browser verification

The full script is
`/home/efe/Documents/QRadar-Dash-work/ui32/verify.mjs`. It creates its own CDP tab and sends real
pointer and keyboard input. The acceptance-driving portion is quoted here (the full file also
contains the navigation, wait, screenshot, overflow and network helpers):

```js
await send("Input.dispatchMouseEvent", {
  type: "mousePressed", x: dragHandle.x, y: dragHandle.y,
  button: "left", clickCount: 1,
});
for (let step = 1; step <= 8; step += 1) {
  await send("Input.dispatchMouseEvent", {
    type: "mouseMoved",
    x: dragHandle.x + (cell * 1.1 * step) / 8,
    y: dragHandle.y,
    button: "none",
    buttons: 1,
  });
  await sleep(25);
}
await waitFor("document.querySelector('.react-grid-placeholder')", "drag placeholder");
await screenshot("mid-drag-placeholder-1440x900.png");
await send("Input.dispatchMouseEvent", {
  type: "mouseReleased", x: dragHandle.x + cell * 1.1, y: dragHandle.y,
  button: "left", clickCount: 1,
});
const dragAfter = await geometry("kpi.total_eps");
if (dragAfter.x === dragBefore.x && dragAfter.y === dragBefore.y) {
  throw new Error("Pointer drag did not change a cell");
}

await send("Input.dispatchMouseEvent", {
  type: "mousePressed", x: handle.x, y: handle.y,
  button: "left", clickCount: 1,
});
for (let step = 1; step <= 8; step += 1) {
  await send("Input.dispatchMouseEvent", {
    type: "mouseMoved",
    x: handle.x + (cell * 1.1 * step) / 8,
    y: handle.y + (36 * step) / 8,
    button: "none",
    buttons: 1,
  });
  await sleep(25);
}
await send("Input.dispatchMouseEvent", {
  type: "mouseReleased", x: handle.x + cell * 1.1, y: handle.y + 36,
  button: "left", clickCount: 1,
});
const resizeAfter = await geometry("kpi.log_sources");
if (resizeAfter.w === resizeBefore.w && resizeAfter.h === resizeBefore.h) {
  throw new Error("Pointer resize did not change a cell");
}

await pressNamed("Move Recent offenses left");
const movedByKey = await geometry("table.recent_offenses");
if (movedByKey.x !== stepBefore.x - 1) {
  throw new Error(`Keyboard move was ${stepBefore.x} -> ${movedByKey.x}`);
}
await pressNamed("Resize Recent offenses shorter");
const resizedByKey = await geometry("table.recent_offenses");
if (resizedByKey.h !== movedByKey.h - 1) {
  throw new Error(`Keyboard resize was ${movedByKey.h} -> ${resizedByKey.h}`);
}

const badConsole = consoleMessages.filter(
  (message) => /hydration|did not match|uncaught|error:/i.test(message),
);
if (badConsole.length) throw new Error(`Browser console errors: ${badConsole.join(" | ")}`);
if (openDelta > 1 || closeDelta > 1) {
  throw new Error(`Customize geometry moved ${openDelta}/${closeDelta}px`);
}
```

Exact output from `logs/verify-output.txt` (the same JSON remains in that log):

```json
{
  "pointerDrag": {
    "before": {"x": 0, "y": 0, "w": 3, "h": 7, "left": 264, "top": 184, "width": 273, "height": 240},
    "after":  {"x": 1, "y": 0, "w": 3, "h": 7, "left": 359, "top": 184, "width": 273, "height": 240}
  },
  "pointerResize": {
    "before": {"x": 3, "y": 7, "w": 3, "h": 7, "left": 553, "top": 436, "width": 277, "height": 240},
    "after":  {"x": 3, "y": 7, "w": 4, "h": 8, "left": 549, "top": 436, "width": 369, "height": 276}
  },
  "keyboardMove": {
    "before": {"x": 6, "y": 42, "w": 6, "h": 15, "left": 835, "top": 421, "width": 558, "height": 528},
    "after":  {"x": 5, "y": 42, "w": 6, "h": 15, "left": 739, "top": 421, "width": 559, "height": 528}
  },
  "keyboardResize": {
    "before": {"x": 5, "y": 42, "w": 6, "h": 15, "left": 739, "top": 421, "width": 559, "height": 528},
    "after":  {"x": 5, "y": 42, "w": 6, "h": 14, "left": 739, "top": 421, "width": 559, "height": 492}
  },
  "geometryDeltaPx": {"open": 0.75, "close": 0},
  "initialScripts": [
    "http://127.0.0.1:3132/_next/static/chunks/webpack-00149c2a85d2bf45.js",
    "http://127.0.0.1:3132/_next/static/chunks/2619-04bc32f026a0d946.js",
    "http://127.0.0.1:3132/_next/static/chunks/main-app-799d70287355f3e7.js",
    "http://127.0.0.1:3132/_next/static/chunks/4bd1b696-100b9d70ed4e49c1.js",
    "http://127.0.0.1:3132/_next/static/chunks/960-3b0edf59c068b844.js",
    "http://127.0.0.1:3132/_next/static/chunks/app/layout-8aae47df59339cbf.js",
    "http://127.0.0.1:3132/_next/static/chunks/app/error-bc84e4200cb0e2d7.js",
    "http://127.0.0.1:3132/_next/static/chunks/381-4eacf4503600a936.js",
    "http://127.0.0.1:3132/_next/static/chunks/app/page-2da5caa4824362e1.js",
    "http://127.0.0.1:3132/_next/static/chunks/1255-cf02c4775860a5ab.js",
    "http://127.0.0.1:3132/_next/static/chunks/8975-04562f10d8901a4c.js",
    "http://127.0.0.1:3132/_next/static/chunks/3383.aa709daea4a8a140.js",
    "http://127.0.0.1:3132/_next/static/chunks/1437.d63b78d91c5cef31.js",
    "http://127.0.0.1:3132/_next/static/chunks/3989.e70eff453726d67d.js",
    "http://127.0.0.1:3132/_next/static/chunks/5734.fb6d266e81f7780d.js",
    "http://127.0.0.1:3132/_next/static/chunks/6445-59334e75ddf37b6f.js",
    "http://127.0.0.1:3132/_next/static/chunks/2767-09a6ab628ef3bb43.js",
    "http://127.0.0.1:3132/_next/static/chunks/9279.cd95d8d37edd4bcd.js",
    "http://127.0.0.1:3132/_next/static/chunks/4245.c7375a81654774ee.js",
    "http://127.0.0.1:3132/_next/static/chunks/3897.dada509fd8f8a64a.js",
    "http://127.0.0.1:3132/_next/static/chunks/6437.e3314c4605ab790e.js",
    "http://127.0.0.1:3132/_next/static/chunks/1356-e23e6b0395060c7e.js",
    "http://127.0.0.1:3132/_next/static/chunks/384.fef8e7096bb42303.js",
    "http://127.0.0.1:3132/_next/static/chunks/8421.3824cd417e7fe46d.js",
    "http://127.0.0.1:3132/_next/static/chunks/9348.3491e3236a2b1ebd.js",
    "http://127.0.0.1:3132/_next/static/chunks/9572.ae38886904ed850d.js",
    "http://127.0.0.1:3132/_next/static/chunks/509.390fff4dedfeb955.js",
    "http://127.0.0.1:3132/_next/static/chunks/9231-293c972368dff020.js",
    "http://127.0.0.1:3132/_next/static/chunks/app/behavior/error-ef8122fb30c1a595.js",
    "http://127.0.0.1:3132/_next/static/chunks/app/anomalies/error-1086443b1b36ada5.js",
    "http://127.0.0.1:3132/_next/static/chunks/app/fleet/error-5ad4084b13312cd5.js",
    "http://127.0.0.1:3132/_next/static/chunks/app/rules/statistics/error-c7c9f06572ddd4e8.js",
    "http://127.0.0.1:3132/_next/static/chunks/app/admin/search-cadence/error-a404d55af174150f.js",
    "http://127.0.0.1:3132/_next/static/chunks/app/admin/classification-rules/error-f60d5155b6185c14.js",
    "http://127.0.0.1:3132/_next/static/chunks/app/admin/waf-sources/error-f102e231f26d5bc1.js",
    "http://127.0.0.1:3132/_next/static/chunks/app/admin/ips-sources/error-9da4c0f145b37400.js",
    "http://127.0.0.1:3132/_next/static/chunks/app/admin/outage-policy/error-ae7ff1f7156d0770.js"
  ],
  "customizeScripts": [
    "http://127.0.0.1:3132/_next/static/chunks/9948.d99ec911235a304b.js",
    "http://127.0.0.1:3132/_next/static/chunks/7732.32953bdebcac960d.js"
  ],
  "hydrationWarnings": [],
  "screenshots": [
    "customize-1440x900.png",
    "customize-1280x800.png",
    "mid-drag-placeholder-1440x900.png",
    "home-after-drag-1440x900.png",
    "home-after-resize-1440x900.png",
    "read-1024x768.png"
  ]
}
```

The initial read-mode request list contains neither customize chunk. After Customize opens, the
two chunks total 68,601 bytes raw and 19,212 bytes gzip:

- editor bridge `9948`: 3,292 bytes raw / 1,520 bytes gzip;
- React Grid Layout dependency `7732`: 65,309 bytes raw / 17,692 bytes gzip.

The production build reports `/` as `10.1 kB` route size and `124 kB` first-load JS. The CDP run
found no hydration/console error on initial read or editor entry, no horizontal document overflow
at any required viewport, and no editor at 1024 px. Opening customize mode changed the largest
widget edge by 0.75 px; closing it changed the largest edge by 0 px, both within the 1 px limit.

### Screenshots inspected

- `/home/efe/Documents/QRadar-Dash-work/ui32/png/customize-1440x900.png` — populated 12-column
  editor, compact title-row controls, session-only statement and no page overflow.
- `/home/efe/Documents/QRadar-Dash-work/ui32/png/customize-1280x800.png` — the same editor and
  geometry at the smaller required desktop viewport; widget bodies scroll internally where needed.
- `/home/efe/Documents/QRadar-Dash-work/ui32/png/mid-drag-placeholder-1440x900.png` — active pointer
  drag with the token-coloured placeholder in the source cell.
- `/home/efe/Documents/QRadar-Dash-work/ui32/png/home-after-drag-1440x900.png` — read mode after Done,
  retaining the moved Total EPS widget without edit chrome.
- `/home/efe/Documents/QRadar-Dash-work/ui32/png/home-after-resize-1440x900.png` — read mode after
  Done, retaining the resized Log sources widget.
- `/home/efe/Documents/QRadar-Dash-work/ui32/png/read-1024x768.png` — unchanged two-column read view
  with “Widen the window to customize”; no editor is mounted.

I opened and checked every PNG after the final capture. Text is legible, the populated widgets use
their designed states, edit chrome does not cover primary values, the drag placeholder is clear,
and there is no clipping or page-level horizontal overflow at the named viewports.

## Tests and break-its

The new/expanded focused suites contain 60 passing tests: 34 layout, 20 dashboard view, and 6 grid
editor tests. Before the final run I deliberately broke and restored these independent guards:

1. Changed the wide breakpoint from `>= 900` to `> 900`: one breakpoint test failed.
2. Removed widget width from the move-right clamp: two right-edge/12-column tests failed.
3. Ignored `minW` while narrowing: one minimum-width test failed.
4. Removed the changed widget's locked placement during compaction: eleven collision/compaction
   tests failed.
5. Forced the Move-left button enabled at its bound: one grid control test failed.
6. Forced the Taller button enabled at its maximum: one grid control test failed.

After restoration, the focused run was 3 files passed and 60 tests passed, and the full suite below
also passed.

## Gates

- `npm view react-grid-layout@2.2.4` — first attempt could not write the default npm cache; rerun
  with `/home/efe/Documents/QRadar-Dash-work/ui32/npm-cache` succeeded and reported `2.2.4`, React
  peer `>=16.3.0`, and unpacked size 447,579 bytes.
- `npm install --save-exact react-grid-layout@2.2.4` — exit 0; `added 6 packages`. npm also reported
  22 audit findings (6 moderate, 13 high, 3 critical); no out-of-scope audit fix was run.
- Two initial gate-wrapper attempts exited 127 with `bash: npm: command not found` because the
  runner passed `bash -lc` arguments incorrectly; npm did not execute. The direct commands below
  are the authoritative final runs.
- `PATH=/home/efe/.local/node/bin:$PATH npm run lint` — exit 0:
  `✔ No ESLint warnings or errors`.
- `PATH=/home/efe/.local/node/bin:$PATH npm run typecheck` — exit 0; `tsc --noEmit` emitted no
  diagnostics.
- `PATH=/home/efe/.local/node/bin:$PATH npm test` — exit 0:
  `Test Files  144 passed (144)` and `Tests  2119 passed (2119)`.
- `PATH=/home/efe/.local/node/bin:$PATH npm run build` — exit 0:
  `✓ Compiled successfully in 5.9s` and `✓ Generating static pages (25/25)`.
- `node /home/efe/Documents/QRadar-Dash-work/ui32/verify.mjs` — exit 0; pointer drag, pointer
  resize, both keyboard changes, geometry, network split, console, viewport and screenshot
  assertions all passed; exact output is quoted above and retained in the log.
- `python3 -m py_compile .../fixture/server.py` plus direct route smoke checks — exit 0;
  `/api/v1/fleet/sources 200 1 1` and `/api/v1/rules 200 1 1` for filtered searches.
- `git diff --check` — exit 0, no output.
- Backend Ruff/pytest and integration tests were not run: this task changes only frontend and
  external verification assets, and no integration database URL was supplied.

All Next, fixture and Chrome processes started for verification were stopped individually. A final
socket check showed no listener on ports 3132, 8132 or 9132. No broad process-kill command was used.

## Open points

- No task acceptance item remains open.
- The full Vitest run still prints pre-existing React `act(...)` warnings in AutoRefresh and the
  two source-picker suites; all affected tests pass, and none of those files is in this task's
  allowed scope.
- npm's audit findings were reported but not modified because the task authorizes only the exact
  React Grid Layout dependency install.

## Round 2 — compact customize controls

### Files changed in Round 2

- `frontend/src/widgets/Grid.tsx` — replaces the separate Arrange and Remove actions with one
  explicit disclosure button. Its visible label is the compact, non-wrapping `Edit`; its accessible
  name remains `Arrange [widget]`. The panel contains all eight steppers and the required
  `Remove [widget]` action.
- `frontend/src/widgets/Grid.test.tsx` — opens the disclosure before exercising its controls and
  adds coverage that Remove is inside the one named arrangement cluster.
- `frontend/src/widgets/DashboardView.tsx` — passes removal into the grid disclosure and removes
  the second title-row action from unavailable-widget frames.
- `frontend/src/widgets/DashboardView.test.tsx` — opens `Arrange Alpha` before the two tests that
  press `Remove Alpha`; editor-ready assertions now wait for the disclosure trigger.
- `frontend/src/components/home/WidgetFrame.tsx` — renders only the supplied arrangement cluster
  in customize mode instead of appending a separate Remove button.
- `frontend/src/components/home/WidgetFrame.test.tsx` — the existing customize-frame test now
  supplies and opens a disclosure before pressing its named Remove action.
- `frontend/src/widgets/dashboard.css` — makes the compact trigger a non-wrapping, unpadded text
  control and styles Remove inside the overlay panel.
- `/home/efe/Documents/QRadar-Dash-work/ui32/verify.mjs` — measures every template widget in read
  and customize modes at both desktop viewports, asserts the Round 2 limits, and captures the open
  disclosure.
- `/home/efe/Documents/QRadar-Dash-work/ui32/logs/verify-output.txt` — complete final Round 2 CDP
  output.
- `/home/efe/Documents/QRadar-Dash-work/ui32/png/*.png` — all captures regenerated from the final
  production build.

### Control choice

The title row now has exactly one customize control where read mode has one right-hand link. The
visible `Edit` label is shorter than the shortest template link (`Fleet`), has no horizontal
padding, and is forced to `white-space: nowrap`, so the cluster cannot split. Its accessible name,
`Arrange [widget]`, describes the action more fully without consuming title-row width. Opening it
reveals Move, Resize and `Remove widget`; Remove retains the widget-specific accessible name. The
panel remains an overlay, so opening it does not change the widget's grid geometry or body height.

The longest visible step labels were shortened to `Narrow` and `Shorten` after visual review while
their accessible names remain `Resize [widget] narrower` and `Resize [widget] shorter`.

### Measured browser acceptance

The final `/home/efe/Documents/QRadar-Dash-work/ui32/verify.mjs` includes this measurement and
assertion logic:

```js
async function widgetDensity() {
  return evaluate(`[...document.querySelectorAll('.dg-item')].map((item, index) => {
    const frame = item.querySelector(':scope > .kpi, :scope > .tw');
    const header = frame?.querySelector(':scope > .kpi-head, :scope > .tw-head');
    const body = frame?.querySelector(':scope > .kpi-body, :scope > .tw-body');
    if (!header || !body) throw new Error('Widget frame is missing density elements');
    return {
      key: item.dataset.type + ':' + index,
      type: item.dataset.type,
      title: header.querySelector('h2')?.textContent.trim(),
      titleRowHeight: header.getBoundingClientRect().height,
      bodyOverflow: body.scrollHeight > body.clientHeight,
      bodyScrollHeight: body.scrollHeight,
      bodyClientHeight: body.clientHeight,
    };
  })`);
}

function assertCustomizeDensity(viewportName, read, customize) {
  if (read.length !== customize.length) {
    throw new Error(`${viewportName} widget count changed ${read.length} -> ${customize.length}`);
  }
  for (const before of read) {
    const after = customize.find((item) => item.key === before.key);
    if (!after) throw new Error(`${viewportName} missing customize measurement for ${before.key}`);
    if (!before.bodyOverflow && after.bodyOverflow) {
      throw new Error(`${viewportName} customize added body overflow to ${before.title}`);
    }
    if (after.titleRowHeight > before.titleRowHeight + 1) {
      throw new Error(`${viewportName} customize made ${before.title} header taller`);
    }
  }
}
```

The following is the exact per-widget measurement output extracted from the final log. Columns are
viewport, widget, read title-row height, customize title-row height, read body overflow and
customize body overflow:

```text
1440x900  kpi.total_eps             18    18    false  false
1440x900  kpi.log_sources           18    18    false  false
1440x900  kpi.offenses              32.5  18    false  false
1440x900  kpi.anomalies             18    18    false  false
1440x900  chart.eps_fleet_total     28    28    false  false
1440x900  chart.eps_by_class        52    21    false  false
1440x900  table.top_sources         21.5  21    false  false
1440x900  chart.anomaly_severity    21.5  21    false  false
1440x900  health.log_sources        21.5  21    false  false
1440x900  table.recent_anomalies    24    21    false  false
1440x900  table.recent_offenses     21.5  21    false  false
1280x800  kpi.total_eps             18    18    false  false
1280x800  kpi.log_sources           18    18    false  false
1280x800  kpi.offenses              32.5  32.5  false  false
1280x800  kpi.anomalies             32.5  18    false  false
1280x800  chart.eps_fleet_total     28    28    false  false
1280x800  chart.eps_by_class        79.5  21    false  false
1280x800  table.top_sources         21.5  21    false  false
1280x800  chart.anomaly_severity    21.5  21    false  false
1280x800  health.log_sources        21.5  21    false  false
1280x800  table.recent_anomalies    24    21    false  false
1280x800  table.recent_offenses     21.5  21    false  false
```

All 22 comparisons passed. Customize added no body overflow and made no title row taller; the
largest increase was 0 px. The same run retained the original drag, resize, keyboard, geometry,
network, hydration and viewport assertions. Geometry remained 0.75 px on open and 0 px on close,
and `hydrationWarnings` remained empty. The complete 557-line JSON output is retained in
`logs/verify-output.txt`.

### Round 2 screenshots inspected

- `/home/efe/Documents/QRadar-Dash-work/ui32/png/customize-1440x900.png` — one `Edit` action per
  widget; EPS-by-class keeps the Partial pill and action together without a second Remove line.
- `/home/efe/Documents/QRadar-Dash-work/ui32/png/customize-1280x800.png` — KPI titles stay at their
  read-mode height or shorter; values, state rows and sparklines are visible without native body
  scrollbars or clipped final lines.
- `/home/efe/Documents/QRadar-Dash-work/ui32/png/arrange-open-1440x900.png` — the open overlay shows
  all Move and Resize controls plus the named Remove action, with no clipped labels.
- `/home/efe/Documents/QRadar-Dash-work/ui32/png/mid-drag-placeholder-1440x900.png` — refreshed
  pointer-drag placeholder capture with the compact edit trigger.
- `/home/efe/Documents/QRadar-Dash-work/ui32/png/home-after-drag-1440x900.png` — refreshed read view
  retaining the moved widget.
- `/home/efe/Documents/QRadar-Dash-work/ui32/png/home-after-resize-1440x900.png` — refreshed read
  view retaining the resized widget.
- `/home/efe/Documents/QRadar-Dash-work/ui32/png/read-1024x768.png` — refreshed unchanged narrow
  read view with the widen instruction.

I inspected all seven final PNGs at original resolution. The reported crowding, native KPI
scrollbars, clipped content and standalone Remove line are gone.

### Round 2 gates

- `PATH=/home/efe/.local/node/bin:$PATH npm run lint` — exit 0:
  `✔ No ESLint warnings or errors`.
- `PATH=/home/efe/.local/node/bin:$PATH npm run typecheck` — exit 0; `tsc --noEmit` emitted no
  diagnostics.
- `PATH=/home/efe/.local/node/bin:$PATH npm test` — exit 0:
  `Test Files  144 passed (144)` and `Tests  2120 passed (2120)`.
- `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8132/api/v1 INTERNAL_API_BASE_URL=http://127.0.0.1:8132/api/v1 PATH=/home/efe/.local/node/bin:$PATH npm run build`
  — exit 0: `✓ Compiled successfully in 3.5s` and `✓ Generating static pages (25/25)`; `/` is
  `10 kB` with `124 kB` first-load JS.
- `PATH=/home/efe/.local/node/bin:$PATH node verify.mjs` — exit 0; all original and Round 2 CDP
  assertions passed, the log was replaced, and all seven PNGs were recaptured.
- `git diff --check` — exit 0, no output.

Diagnostic runs were also reported rather than hidden: one focused-test invocation used repository-
relative paths from inside `frontend/` and found no files; the corrected invocation ran the suites.
The first disclosure implementation exposed the native `summary` role inconsistently in jsdom, so
focused tests failed and the trigger was replaced by an explicit accessible button. The next run
correctly caught four tests that had not opened the newly conditional panel; those tests were fixed.
An initial browser run used a build without the fixture's public API URL and failed closed on refused
API reads; rebuilding with both documented fixture URL variables produced the successful final run.

All seven final images were reviewed only after that successful final run. Next, the fixture and
Chrome were then stopped individually; ports 3132, 8132 and 9132 had no listeners. The pre-existing
React `act(...)` warnings remain the only test-suite warning and are outside this task's scope.
