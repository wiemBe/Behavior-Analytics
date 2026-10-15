# Task UI-5.3 — WAF behavior pages: review against the redesign and fix

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative: §4, §6, §8, §9, §10), `frontend/src/app/tokens.css`,
`frontend/src/components/ui/controls.css`, and in `docs/UI-REDESIGN-PLAN.md`: **UR.1** (its "Rule
for every entry" and the `/behavior/waf` entry of **UR.1.1**), **UR.2**, **UR.3.3**, **UR.7
Phase 5**, **UR.8.1** and **UR.8.6**. Read `docs/PHASE-B-WAF-BEHAVIOR.md` §27.15 (the WAF analyst
UI's design) for what each number means.

**Method, test rules and the review rulings are those of `docs/tasks/UI-5.5.md`** (sections
"What restyle means here", "Rulings from UI-5.6's review that apply here", "Method" and
"Tests"): read it and follow it, substituting this task's routes, files, ports and paths.
`docs/tasks/UI-5.6.report.md` shows the expected depth of the defect list.

## Routes and states

- `/behavior/waf`: the source table; a source with EXACT and one with LOWER_BOUND measurement;
  a source with no detector evaluation; `INSUFFICIENT_DATA`; the range selector; empty (no WAF
  source configured); failed read.
- `/behavior/waf/sources/[id]`: the request chart with its expected band; the endpoint table with
  endpoint state `null` ("no detector has evaluated it", never `NORMAL`), `INSUFFICIENT_DATA`,
  ANOMALOUS; ratios in percent points (not fractions ×100: a past defect); long endpoint paths;
  404; failed reads.

## What to check above all

- Units: requests per bucket, %, pp; tabular, right-aligned. A LOWER_BOUND count is marked as a
  lower bound ("≥"), never shown as exact.
- The expected range is a band; no marker on every point; axes carry units and the zone.
- Long endpoint paths at 1024: readable (wrap or truncate with the full path available), the page
  never scrolls sideways.
- Consistency with `/behavior/sources/[id]` (UI-5.1) and `/anomalies/[id]` (UI-5.2): the same
  kind of block looks the same.

## Pinned tests (UR.1.1 "Keeps")

`app/behavior/waf/page.test.tsx`, `app/behavior/waf/sources/[id]/page.test.tsx`, every
`components/waf/*.test.*` file (including `WafRequestChart.test.tsx`), and the `error`/`loading`
tests under `app/behavior/waf/`. They stay green **unedited**. If a fix needs an edit to one, it
is the wrong fix: stop and report it.

## Screenshots and fixture

- Fixture API in `/home/efe/Documents/QRadar-Dash-work/ui53/fixture/` with the **backend's real
  payload shapes** (`backend/app/schemas/waf.py`; find the `/behavior/waf/*` routes under
  `backend/app/api/routes/`). Start from a copy of `/home/efe/Documents/QRadar-Dash-work/t090/fixture/`
  (WAF details, metrics, baselines); do not edit it in place.
- Ports 3153/8153/9153. Headless Chrome is `/usr/bin/google-chrome`.
- PNGs under `/home/efe/Documents/QRadar-Dash-work/ui53/png/{before,after}/`. Nothing under
  `/tmp`.

## Allowed files

- `frontend/src/app/behavior/waf/**` page files and colocated stylesheets (tests: new tests only)
- `frontend/src/components/waf/*` (new tests only)
- `frontend/src/app/tokens.css` (new tokens only, each justified in the report)
- `docs/tasks/UI-5.3.report.md` (new)

Not `globals.css`, not `components/ui/*`, not `components/behavior/*`, not other pages: if a
shared component is the cause, list it as an open point.

## Gates

From `frontend/` with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim into the report
(AGENTS.md §6 format), with the defect list, the before/after PNG lists and what changed per
route. Do not commit. Stop every process you start, by PID. Never `pkill -f` or `killall`.
