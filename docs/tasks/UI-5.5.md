# Task UI-5.5 — offenses, rules and coverage: review against the redesign and fix

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative: §4, §6, §8, §9, §10), `frontend/src/app/tokens.css`,
`frontend/src/components/ui/controls.css`, and in `docs/UI-REDESIGN-PLAN.md`: **UR.1** (its "Rule
for every entry" and the `/offenses`, `/rules` and `/coverage` entries of **UR.1.2**), **UR.2**,
**UR.3.3**, **UR.7 Phase 5**, **UR.8.1** and **UR.8.6**. Read `docs/tasks/UI-5.6.md` and
**`docs/tasks/UI-5.6.report.md`** (the method and the bar: this task does the same for six other
routes), `docs/tasks/UI-5.6b.md` (the review rulings that followed it, which apply here too), and
**`docs/tasks/T-096.report.md`**: T-096 wrote the characterization tests for `/offenses`,
`/offenses/[id]` and `/coverage`, and recorded two defects as `it.todo`.

## What "restyle" means here

Phase 1 moved every page to the new tokens, typography, shell and shared components. These pages
were written against the old tokens and have not been looked at closely since. Find where they
fall short of the redesign and the guidelines, and fix that in CSS and minimal markup, **plus the
honesty defects T-096 recorded**. Change no request, no permission rule and no behaviour.

Compare each page with the Home, the anomaly pages and the UI-5.6 pages (`/log-sources`,
`/alerts`), which are on the new system: the same kind of thing (a table, a status pill, a time,
an empty notice, a header, an error notice) must look and behave the same way. Inconsistency
between pages is a defect.

## Routes and states

- `/offenses` (list with filters; aggregates; empty with and without a filter; failed read);
  `/offenses/[id]` (normal; null `magnitude`/`severity`/`credibility`/`relevance`; null event,
  flow, source and destination counts; history read failed; 404 "not collected"; non-numeric id).
- `/rules` (health summary and list; filters; failed read); `/rules/[id]` (health history,
  capacity history; missing windows); `/rules/statistics` (sections `null` with a reason;
  `lower_bound` match rates; truncated, failed and missing windows counted apart).
- `/coverage` (summary, techniques, by rule, by data source; `coverage_score` and `confidence`
  null; explicit vs inferred `mapping_provenance`; the "not evaluated" state; failed read).

## The defects T-096 recorded (fix each; turn its `it.todo` into a passing test)

- `/offenses/[id]`: an unmeasured Events/Flows count is drawn as `0` (`o.event_count ?? 0`).
- `/offenses/[id]`: an unmeasured Sources/Destinations count is drawn as `0`
  (`o.source_count ?? 0`).

A missing measurement is "—" (or a stated reason), never `0`. Each `it.todo` becomes an `it(...)`
that fails on the old code (break-it by reverting your fix) and passes on the new. After this
task the suite's `todo` count is 0.

## Rulings from UI-5.6's review that apply here

- A missing value has no unit after it ("—", never "— s").
- A time never wraps; a column whose header says "(UTC)" does not repeat "UTC" in its cells;
  otherwise times carry their zone through the app's formatters (`lib/health`'s
  `formatDateTime` / `formatDateTimeCompact`).
- The page title is `PageHeader` (an `h1`). If a test pins another level, stop and report it;
  do not edit it.
- An error notice says what failed and what the analyst can do, with `role="alert"`.
- Tables sit in `TableScroll`; numbers are right-aligned (`num`) with tabular figures; no page
  overflow at 1024×768.
- UR.1.2 for offenses: magnitude is a bar **with the number beside it**; colour is never the only
  carrier.

## Method

1. **Capture first**, every route and state at 1440×900, 1280×800 and 1024×768, into
   `/home/efe/Documents/QRadar-Dash-work/ui55/png/before/`.
2. **Review:** a defect list in the report (route, viewport, what is wrong, the rule, the fix),
   including the T-096 defects and at least: page overflow; the shared `PageHeader`; literals and
   inline styles; numbers (unit, tabular, right-aligned); times with a timezone; every state
   designed (loading, empty, error, and the honesty states above); pie or donut charts with more
   than five slices; charts with no unit on an axis or a marker on every point; filters
   (`OffenseFilters.tsx`) labelled, keyboard-usable, and consistent with the other pages' controls.
3. **Fix** within the allowed files. Pinned classes stay (UR.8.1): change their CSS, never rename
   them. Keep the DOM order the tests pin.
4. **Capture after** into `…/ui55/png/after/`. Open the PNGs and say per route what changed and
   which PNGs you looked at.

## Tests

- All existing tests stay green. The only existing-test edits allowed are turning T-096's two
  `it.todo` lines into real tests. If any other fix needs a test edit, it is the wrong fix; if a
  test seems wrong, stop and report it.
- Add tests for markup you add. **Break-it** each new or converted test: confirm it fails, then
  restore.

## Screenshots and fixture

- Fixture API in `/home/efe/Documents/QRadar-Dash-work/ui55/fixture/` (outside the repository)
  with the **backend's real payload shapes**: read `backend/app/schemas/` and
  `backend/app/api/routes/` for offenses, rules (health, history, capacity, statistics) and
  coverage; the existing page tests show shapes in use. The UI-5.6 fixture
  (`/home/efe/Documents/QRadar-Dash-work/ui56/fixture/`) shows the harness pattern (a small
  Python server with a control route) and can be copied.
- Ports 3155/8155/9155. Headless Chrome is `/usr/bin/google-chrome`.
- Nothing under `/tmp`.

## Allowed files

- the six routes' page files (`app/offenses/**`, `app/rules/**`, `app/coverage/**`), their
  `page.test.tsx` (only as above, plus new tests), and colocated stylesheets
- `frontend/src/components/rules/*`, `frontend/src/components/OffenseFilters.tsx`, and their
  tests (new tests only)
- `frontend/src/app/tokens.css` (new tokens only, each justified in the report)
- `docs/tasks/UI-5.5.report.md` (new)

Not `globals.css`, not `components/ui/*`, not other pages: if a shared component is the cause,
list it as an open point.

## Gates

From `frontend/` with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache` (the `todo` count must reach 0), `npm run build`. Copy each summary
line verbatim into the report (AGENTS.md §6 format), with the defect list, the before/after PNG
lists and what changed per route. Do not commit. Stop every process you start, by PID. Never
`pkill -f` or `killall`.
