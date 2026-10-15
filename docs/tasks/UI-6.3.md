# Task UI-6.3 — performance of the dashboard (Phase 6 QA)

Read `AGENTS.md` at the repository root first and follow it exactly (§3a). Then read
`docs/UI-REDESIGN-PLAN.md`, **UR.8.3** (rendering, bundle, the budgets) and the UI-6.3 row of
**UR.7 Phase 6**.

Start after Phase 5 is merged. This task **measures and reports**. It fixes only a defect whose
fix is local to one widget or one hook and is covered by a test. Anything larger is a finding
for the orchestrator.

## What to measure

Use headless Chrome (`/usr/bin/google-chrome`) driven over CDP, against a production build
(`npm run build` then `next start`) and a deterministic fixture API. Copy the newest dashboard
fixture, `QRadar-Dash-work/t098/fixture/` or `ui42/`, into
`/home/efe/Documents/QRadar-Dash-work/ui63/fixture/`. Add a seeded dashboard with **24 widgets**
(the limit), mixing every live widget type, at least 8 of them charts.

For each of three dashboards, at 1440×900, cold load and warm load, five runs each, report the
median and the worst:
- the SOC Overview template;
- the 24-widget dashboard;
- the 24-widget dashboard scrolled to the bottom.

1. **Time to the first widget's content.** Measure from navigation start to the first widget
   body that shows data rather than a loading line. Mark the moment with a `MutationObserver`
   or a test id. The budget is under 2 s (UR.8.3).
2. **Long tasks** (`PerformanceObserver` `longtask`): their count and the longest, during the load
   and during a drag in Customize mode. The budget is no long task above 100 ms during a drag.
3. **Requests:**
   - the count and total bytes up to the moment the network is idle;
   - then 60 s with no interaction and refresh `Off`. The budget is no request at idle;
   - then the same 60 s with refresh set to 1 min: requests per refresh, against the number of
     widgets.
4. **ECharts instances.** Count them on the page (for example `echarts.getInstanceByDom` on
   every chart container). Off-screen charts must not be created (UR.8.3), so give the count
   before and after scrolling. Check that each is disposed on unmount: navigate away and back,
   and compare the instance count and the JS heap.
5. **Bundle.** From the `next build` output: the first-load JS of `/` and of the three heaviest
   routes. Check that ECharts is imported modularly (no `echarts` full build in a chunk) and that
   the map code is absent, since `map.geography` is blocked.

## Deliverable

- `docs/tasks/UI-6.3.report.md` (AGENTS.md §6), with one table per measurement: run, value and
  budget, then pass or fail. Every number must come from a run you did.
- Findings: each with the measurement, the likely cause (`file:line`) and a proposed fix. Fix a
  finding only if it is local to one widget or hook, has a test and passes break-it.
- The measurement script lives in `/home/efe/Documents/QRadar-Dash-work/ui63/`, outside the
  repository, with a README so it can be re-run.

## Rules

- Ports 3163/8163/9163.
- Stop every process you start by PID; never `pkill -f` or `killall`.
- Scratch under `/home/efe/Documents/QRadar-Dash-work/ui63/`, never `/tmp`.
- If you fix anything, run the gates from `frontend/`: `npm run lint`, `npm run typecheck`,
  `npx vitest run --no-cache`, `npm run build`. Copy the summary lines verbatim.
- Do not commit.
