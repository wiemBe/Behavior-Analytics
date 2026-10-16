# Task UI-5.1 — behavior overview and source pages: review against the redesign and fix

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative: §3, §4, §5, §6, §8, §9, §10),
`frontend/src/app/tokens.css`, `frontend/src/components/ui/controls.css`, and in
`docs/UI-REDESIGN-PLAN.md`: **UR.1** (its "Rule for every entry" and the `/behavior`,
`/behavior/sources` and `/behavior/sources/[id]` entries of **UR.1.1**), **UR.2**, **UR.3.3**,
**UR.7 Phase 5**, **UR.8.1** and **UR.8.6**.

**Method, test rules and the review rulings are those of `docs/tasks/UI-5.5.md`** (sections
"What restyle means here", "Rulings from UI-5.6's review that apply here", "Method" and
"Tests"): read it and follow it, substituting this task's routes, files, ports and paths.
`docs/tasks/UI-5.6.report.md` shows the expected depth of the defect list.

## Routes and states

- `/behavior`: the four primary cards; the worklist; `INSUFFICIENT_DATA` banner; unbaselined
  expected as "—"; evidence pending vs failed; empty (nothing active); failed reads.
- `/behavior/sources`: the inventory table with its filter row; `INSUFFICIENT_DATA` state; null
  expected and deviation; empty; failed read; a long source name.
- `/behavior/sources/[id]`: the six answers in order; the four metrics; the volume chart with
  `COMPLETE`, `PARTIAL` and `MISSING` buckets; baseline `is_reliable=false` ("Still learning");
  a measured zero (`0.00`) next to an unmeasured value ("—"); one and several anomalies; 404;
  failed reads.

## Known defects to fix (plus everything your review finds)

- **Overlapping anomaly bands stack their alpha** on `/behavior/sources/[id]`'s volume chart: two
  incidents covering the same interval paint a darker band, which reads as a stronger signal.
  Overlaps must render at one, constant intensity (for example merge overlapping intervals into
  one band, or draw bands in a way that does not accumulate). Pin it with a test.
- The expected range is a **band**, never three lines (AGENTS.md §3a); no marker on every point;
  axes carry units ("EPS", "Time (UTC)").

## Pinned tests (UR.1.1 "Keeps")

`app/behavior/page.test.tsx`, `app/behavior/sources/page.test.tsx`,
`app/behavior/sources/[id]/page.test.tsx`, `VolumeChart.test.tsx`, `HealthDistribution.test.tsx`,
`StatusBadge.test.tsx` and the `error`/`loading` tests under `app/behavior/`. They stay green
**unedited**. If a fix needs an edit to one, it is the wrong fix: stop and report it.

## Screenshots and fixture

- Fixture API in `/home/efe/Documents/QRadar-Dash-work/ui51/fixture/` with the **backend's real
  payload shapes** (`backend/app/schemas/`; `backend/app/api/routes/anomalies.py` serves the
  `/behavior/*` and `/anomalies*` routes, `log_sources.py` the `/log-sources/*` ones). The reusable fixture in
  `/home/efe/Documents/QRadar-Dash-work/t087/fixture/fixture_api.py` (anomalies, IPS) is a
  starting point; copy it, do not edit it in place.
- Ports 3151/8151/9151. Headless Chrome is `/usr/bin/google-chrome`.
- PNGs under `/home/efe/Documents/QRadar-Dash-work/ui51/png/{before,after}/`. Nothing under
  `/tmp`.

## Allowed files

- `frontend/src/app/behavior/page.tsx`, `frontend/src/app/behavior/sources/**` (page files and
  colocated stylesheets; tests: new tests only)
- `frontend/src/components/behavior/` files used by these routes **except** `Incident*`,
  `Evidence*`, `Dimension*`, `Lifecycle*`, `Contributor*` and `AnomalyFilters*` (those are
  UI-5.2's); new tests only
- `frontend/src/app/tokens.css` (new tokens only, each justified in the report)
- `docs/tasks/UI-5.1.report.md` (new)

Not `globals.css`, not `components/ui/*`, not `app/behavior/waf/**` (UI-5.3), not other pages:
if a shared component is the cause, list it as an open point.

## Gates

From `frontend/` with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim into the report
(AGENTS.md §6 format), with the defect list, the before/after PNG lists and what changed per
route. Do not commit. Stop every process you start, by PID. Never `pkill -f` or `killall`.
