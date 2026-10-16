# Task UI-6.7 — Phase 6 performance follow-ups: lazy charts and no preflight on reads

Read `AGENTS.md` at the repository root first and follow it exactly, §3a and §4. Then read
`docs/UI-REDESIGN-PLAN.md` **UR.8.3** and `docs/tasks/UI-6.3.report.md` in full: this task fixes
its findings **F1** and **F3**, and you (or another agent) measured them there. F2 (retention on
full document loads) and F4 (a seed constraint) are not in this task.

## 1. Off-screen charts are not created (F1, UR.8.3)

UR.8.3: "each is created on mount, disposed on unmount, and **off-screen charts are not created**
(`IntersectionObserver`)". Today every placed widget mounts at once and every chart widget calls
`echarts.init` on its first commit (`widgets/DashboardView.tsx` read grid, `widgets/registry.ts`,
`components/home/useEChart.ts`, `components/waf/WafRequestChart.tsx`).

- Add one shared hook (for example `useInView`, `IntersectionObserver` with a `rootMargin` of
  about one viewport) and use it at **one** call site in the read grid (`PlacedWidget` or the
  grid in `DashboardView.tsx`): until a widget first comes near the viewport, render the
  widget frame with its title and the existing loading line (no skeleton, guidelines §8);
  once it has been near the viewport, mount the body and keep it mounted.
- Customize mode is unchanged: every widget stays mounted there (dragging needs real sizes).
- No layout shift: the frame keeps its grid size while the body is deferred.
- Where `IntersectionObserver` does not exist (the test environment, old browsers), render
  everything at once, as today.
- Tests: the hook (in view, out of view, no IO support); the grid defers an off-screen chart and
  mounts it when it intersects (mock `IntersectionObserver`); customize mode mounts everything.
  Break-it each.

## 2. No CORS preflight on reads (F3)

`frontend/src/lib/api.ts` sends `Content-Type: application/json` on every request, including
bodyless `GET`s, so every cross-origin read is preflighted (`request` around line 38 and
`fleetRequest` around line 1784). Send `Content-Type` only when the request has a body. Keep
`cache: "no-store"`. Pin both helpers' headers with tests (a `GET` has no `Content-Type`; a
`POST`/`PATCH` with a body has it; a caller's own header still wins). Break-it each.

## 3. Measure again

Re-run the UI-6.3 harness (`/home/efe/Documents/QRadar-Dash-work/ui63/`: `perf.mjs`, the
refresh and CORS probes; read its README) against a build of this worktree. Copy it into
`/home/efe/Documents/QRadar-Dash-work/ui67/` and run it there with ports 3167/8167/9167. Report,
before (UI-6.3's numbers) and after, in the same tables: ECharts instances on first paint and
after scrolling to the bottom (§5.1), time to the first widget's content (§2), long tasks during
a drag (§3), requests per refresh and preflights (§4.4), idle requests (§4.2). Every budget that
passed must still pass. Capture the 24-widget dashboard at 1440×900, 1280×800 and 1024×768 at
the top and scrolled to the bottom, open the PNGs, and say what you saw (no blank frames left
behind after scrolling).

## Allowed files

- `frontend/src/widgets/DashboardView.tsx` (the one call site), a new hook under
  `frontend/src/hooks/`
- `frontend/src/lib/api.ts` (the two header lines only)
- new tests; `docs/tasks/UI-6.7.report.md` (new)

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim (AGENTS.md §6).
Stop every process you start by PID. Do not commit. Nothing under `/tmp`.
