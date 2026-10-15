# Task UI-5.6b — log sources, searches and alerts: the follow-ups from UI-5.6's review

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative: §4, §6, §8, §9, §10), `frontend/src/app/tokens.css`,
`docs/tasks/UI-5.6.md` (the parent task) and **`docs/tasks/UI-5.6.report.md`** (what it did and
its open points). UI-5.6 is merged; this task fixes what the orchestrator's review of its
screenshots found, plus the open points the orchestrator has now ruled on.

## Fixes (each with a test, each test break-it checked)

1. **No unit after a missing value.** `/log-sources/[id]` renders "Expected interval: — s" when
   `expected_interval_seconds` is null. A missing value is "—" alone; the unit appears only with a
   number. Check every "label: value unit" line on the six routes for the same defect.
2. **Stale placeholder copy.** `/log-sources/[id]` still says "EPS trend, anomaly timeline and
   baseline overlay charts (Apache ECharts) arrive in Phase 2 …". Phase 2 shipped. Replace it with
   a plain link to where those charts are (`/behavior/sources/[id]`; confirm in the code which id
   that route takes and that it is the same source, and say how in the report). If the mapping
   is not one-to-one, drop the sentence instead. **The orchestrator authorises editing the test
   that pins the old sentence**; list the edit in the report.
3. **`h1` on the detail pages.** `/log-sources/[id]` and `/alerts/[id]` keep a local `h2` styled
   as a page title because their tests pin `level: 2`. Use `PageHeader` (an `h1`) as the other
   routes do. **The orchestrator authorises changing those two assertions to `level: 1`**; no other
   edit to them. Remove the local `.detail-header .page-title` imitation.
4. **QRadar status tones** (UI-5.6 open point 7, ruled): `SUCCESS` → `ok`; `WARNING` → `warn`;
   `ERROR` → `crit`; any other reported value (for example `DISABLED`, `NA`) and null → neutral,
   labelled with the value as QRadar reported it (null reads "unknown"). Same rule on the list and
   the detail page, through one shared function inside the allowed files.
5. **Delivery table times at 1024.** On `/alerts/[id]` the Sent and Next-attempt cells break into
   three lines ("10-14 / 12:59 / UTC"). A time never wraps (`nowrap`, as on every other table);
   the Error column absorbs the wrap. The table must still fit 765 px at 1024×768 with no
   sideways scroll of the page.
6. **"UTC" said twice.** A column header that already says "(UTC)" must not repeat "UTC" in every
   cell (the `/searches/[id]` execution history: "Started (UTC)" and "10-14 17:55 UTC"). Keep the
   zone in the header and drop it from the cells of that column; this also stops the wrap at 1024
   (UI-5.6 open point 5). Apply the same rule to any other table on the six routes.
7. **The result trend's time axis is not proportional.** `ResultTrend.tsx` uses an ECharts
   `category` x-axis, so runs 15 minutes apart and runs 4 minutes apart are drawn the same
   distance apart, and the gaps the caption talks about are not to scale. Use a `time` axis: x is
   the run's start time, the version-change markers and the threshold line stay, a truncated run
   still breaks the line (no zero, no interpolation across it), labels carry the zone or the axis
   name says "Time (UTC)", and no marker is drawn on every point. Tests pin: the axis type, a
   gap where a truncated run is, and the markers' positions as times.
8. **Confirm before Resolve** (UI-5.6 open point 4, ruled). Resolving is terminal, so the Resolve
   button opens an inline confirmation in the Operator Actions card ("Resolve this alert? A
   resolved alert cannot be reopened." with "Resolve alert" and "Cancel"), not `window.confirm`.
   Acknowledge and "Run now" stay single-click. **The orchestrator authorises editing the
   `AlertActions` tests whose click-through now needs the confirm step**: each such edit adds the
   confirm click and changes nothing else; list each in the report. Focus moves into the
   confirmation when it opens and back to the Resolve button on Cancel.

## Not in this task

- `Evidence.tsx` (inline styles, values without units): it belongs to UI-5.2.
- `loading.tsx` / `error.tsx` for these routes: UI-5.8.
- Empty-notice wording pinned by tests ("No alerts.", "No scheduled searches defined yet."): left.

## Screenshots and fixture

- Reuse the UI-5.6 harness in `/home/efe/Documents/QRadar-Dash-work/ui56/` (fixture in
  `fixture/`, `capture.mjs`, `cycle.sh`); copy it to `/home/efe/Documents/QRadar-Dash-work/ui56b/`
  and work from the copy. The fixture must keep the backend's real payload shapes; add a log
  source with a null `expected_interval_seconds`, one with `WARNING`, one with `DISABLED`, and
  search executions at uneven intervals (for the time axis).
- Ports 3149/8149/9149. Headless Chrome is `/usr/bin/google-chrome`.
- PNGs of the changed routes and states at 1440×900, 1280×800 and 1024×768 under
  `/home/efe/Documents/QRadar-Dash-work/ui56b/png/`. **Open them** and say in the report what you
  looked at. Nothing under `/tmp`.

## Allowed files

- the six routes' page files, their `page.test.tsx` (only the authorised edits above, plus new
  tests), and their colocated stylesheets
- `frontend/src/components/AlertActions.tsx`, `ResultTrend.tsx`, `RunSearchButton.tsx`,
  `frontend/src/components/searches/*`, and their tests (new tests, plus the authorised
  `AlertActions` edits)
- `frontend/src/app/tokens.css` (new tokens only, each justified)
- `docs/tasks/UI-5.6b.report.md` (new)

## Gates

From `frontend/` with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim into the report
(AGENTS.md §6 format). Do not commit. Stop every process you start, by PID. Never `pkill -f` or
`killall`.
