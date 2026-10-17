# Task PW-7 — Phase G UI, the read side: `/behavior/watches`, the watch page, PW rows in `/anomalies`

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a above all** (the
frontend quality bar; screenshots are part of acceptance). Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative), `frontend/src/app/tokens.css`, and
`docs/PHASE-G-PROPERTY-WATCH.md` §G.8.0 (the reading table: what is a measured zero, a lower
bound, unknown, not an observation), §G.8.1, §G.11, §G.12 and **§G.13** (binding).

**The API is built (PW-5, PW-5b) and is the contract.** Read
`backend/app/api/routes/property_watches.py`, `backend/app/schemas/property_watch.py`, the
`PW_VALUE` additions in `backend/app/schemas/anomaly.py`, and `docs/tasks/PW-5.report.md` and
`docs/tasks/PW-5b.report.md` for the exact shapes. **No backend change in this task**: if the UI
needs one, stop and report.

## Build

1. **Navigation.** "Property watches" → `/behavior/watches` in the **Behavior** group of
   `frontend/src/lib/nav.ts`; pages under it win by longest match like `/behavior/sources`
   (§G.13.1). Text only (D-11).
2. **`/behavior/watches`, the list** (§G.13.2): the table and its columns, attention rows first,
   status and reason in the second column, coverage with "at least" on a lower bound, last bucket
   in UTC with the age after it, the latency sentence once. No KPI row. A page-level notice when
   `collection_enabled` is false (the setting named) and when alerting is in shadow mode. The
   **New watch** action and the editor are PW-8's: leave no link to a route that does not exist
   yet; say in the report where PW-8 adds it.
3. **`/behavior/watches/[id]`, the watch page** (§G.13.3), read only: the header line; the
   **series chart** (a new shared component, `components/propertywatch/`, that PW-8's preview and
   PW-9's widget will reuse: design its props for that); the values table; coverage and the
   unparsed remainder on the same time axis; the honesty line; windows 1 h, 6 h, 24 h, 7 d. Tabs:
   **Values** (default), **Incidents** (this watch's `/anomalies` rows and their lifecycle),
   **Definition** (scope membership from `GET /property-watches/{id}/scope`: groups walked,
   inventory age, membership changes, the member names; the cardinality caps; the generated AQL
   in a collapsed `CodePanel`, read only, rendered as text). The **Threshold rules** tab needs
   PW-6 and the **Edit** disclosure needs PW-8: neither is built here, and no placeholder is
   drawn.
4. **`/anomalies` and `/anomalies/[id]`**: the `PW_VALUE` subject kind in the filter, its row
   (watch name, value text, detector, state) and an investigation view that links to the watch
   page with the value selected. The value text is shown to every `read:*` caller (D-22).
5. **Permissions.** A route that `read:property_watches` forbids renders the forbidden state
   naming the permission, not an error.

## The series chart (§G.13.3 — honesty is its contract)

Use the platform's chart wrappers and tokens (`VolumeChart`, `WafRequestChart` are the
precedents); no library-default style.
- At most **five** series, in `--chart-cat-1` to `--chart-cat-5`, never a status hue. The y-axis
  says "events per 5 min"; times carry UTC.
- An uncollected bucket and an **unknown zero are gaps** (`connectNulls: false`); a **measured
  zero is drawn as 0**; a `LOWER_BOUND` point reads "at least n" in the tooltip and the text
  table. Never invent continuity.
- **The expected range is one band, only when a single value is selected**; with several, the
  band is off and one line under the chart says so. Incident spans are shaded.
- The default selection is the pinned values, then the top values of the latest bucket.
- Everything the chart carries is also in the table as text (guidelines §10).
- **Values are attacker-controlled text** (a username from a log line). ECharts renders a
  tooltip `formatter`'s returned string as HTML: every value, watch name and property name goes
  through `echarts.format.encodeHTML`, and React renders them as text everywhere else. Pin it with
  a test that feeds `<img src=x onerror=…>` as a value (T-117).

## States and tests

Design every state (§G.13.6, AGENTS.md §3a): loading; empty (what a watch is, that none exist);
error (what failed, what to do); forbidden; collection off; shadow mode; every `PwWatchStatus`
with its reason; `INSUFFICIENT_DATA`, learning ("n of 168 cells"), `PARTIAL`, `TRUNCATED`,
`NOT_COLLECTED`, `NOT_APPLICABLE`, `SCOPE_STALE`, `SCOPE_TOO_LARGE`, a membership change, stale.
The chart's gap rules each have a test (no zero at an unknown bucket, a measured zero drawn as 0,
"at least n" on a lower bound, the band only for one value, at most five series, categorical
tokens only). Table figures right-aligned with tabular figures and a unit. Vitest covers each
state; break-it each and report which failed. `colourLiterals.test.ts` and the contrast tests
stay green.

## Screenshots

Capture `/behavior/watches` (several statuses; empty; collection off), `/behavior/watches/[id]`
in each honesty state (exact; lower-bound buckets; learning; `NOT_APPLICABLE`; scope stale; one
value selected with its band; five values), its Definition and Incidents tabs, and `/anomalies`
with a PW row, at 1440×900, 1280×800 and 1024×768 with headless Chrome
(`/usr/bin/google-chrome`), against a deterministic fixture API that serves the **real** PW-5 and
PW-5b payload shapes. Fixture, capture script, PNGs and scratch go under
`/home/efe/Documents/QRadar-Dash-work/pw7/`, never `/tmp`. Ports 3117/8117/9117. Open the PNGs and
say what you saw; list their paths in the report. No page-level horizontal overflow at any of the
three viewports (guidelines §9).

## Allowed files

- `frontend/src/app/behavior/watches/**` (new) and its colocated CSS
- `frontend/src/components/propertywatch/**` (new)
- `frontend/src/app/anomalies/**` (the `PW_VALUE` kind only; its investigation view may use
  `components/propertywatch/`)
- `frontend/src/lib/api.ts` (the property-watch calls and types only)
- `frontend/src/lib/nav.ts` (the one entry) and its test
- new tests; `docs/PHASE-G-PROPERTY-WATCH.md` `[BUILT — PW-7]` markers in §G.13.1–§G.13.3;
  `docs/tasks/PW-7.report.md` (new)

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm ci`, `npm run lint`,
`npm run typecheck`, `npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim
(AGENTS.md §6). Stop every process you start by PID. Do not commit.
