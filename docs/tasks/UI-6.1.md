# Task UI-6.1 — the full viewport matrix and overflow measurement (Phase 6 QA)

Read `AGENTS.md` at the repository root first and follow it exactly (§3a). Then read
`docs/PRODUCT-UX-GUIDELINES.md` §9 (no page overflow at 1440×900, 1280×800, 1024×768) and
`docs/UI-REDESIGN-PLAN.md` **UR.7 Phase 6** (the UI-6.1 row) and **UR.8**.

Start after Phase 5 is fully merged (UI-5.8, and UI-5.9 if it exists). This task **measures**;
it fixes only what is a one-line CSS fix inside a route's own stylesheet. Anything larger is a
finding for the orchestrator.

## What to do

1. **Route list:** every `frontend/src/app/**/page.tsx` (32 routes today; list them in the report
   with the source file).
2. **States:** for each route, the normal state plus every honesty state its page can render
   (empty, error, loading, INSUFFICIENT_DATA, PARTIAL, TRUNCATED, NOT_COLLECTED where they exist).
   Build one fixture API with the backend's real payload shapes that can serve all of them, by
   merging the existing fixtures (`QRadar-Dash-work/ui51/`, `ui52/`, `ui53/`, `ui55/`, `ui56b/`,
   `ui41/` or `ui42/`) into `/home/efe/Documents/QRadar-Dash-work/ui61/fixture/`.
3. **Capture** every route × state at 1440×900, 1280×800, 1024×768 with headless Chrome
   (`/usr/bin/google-chrome`), ports 3161/8161/9161; script outside the repository
   (`/home/efe/Documents/QRadar-Dash-work/ui61/`). Full-page PNGs into `…/ui61/png/`.
4. **Measure, per capture**, from the live page (not the PNG):
   - document `scrollWidth` vs `clientWidth` (page overflow; must be equal);
   - every `table` vs its scroll container (does any table scroll sideways inside its region, and
     which columns are hidden at the start position);
   - any element whose right edge is past the viewport;
   - inline `style` attributes in our markup (ignore ones ECharts adds at runtime);
   - text that overlaps other text (bounding boxes of sibling text nodes intersecting);
   - computed `white-space` of every `.nowrap` cell (must be `nowrap` at all three widths).
   Write the results as `…/ui61/matrix.json` and a summary table in the report: route × viewport ×
   state → pass/fail with the failing measurement.
5. **Look** at every failing capture and at least one capture per route at 1024×768, and say what
   you saw. A measurement that passes but looks wrong is a finding too.
6. Fix only one-line CSS defects inside a route's own colocated stylesheet; list everything else as
   findings with route, viewport, state, measurement and a proposed owner.

## Allowed files

- route-colocated stylesheets, for one-line fixes only, each listed in the report
- `docs/tasks/UI-6.1.report.md` (new)

No test edits; no shared CSS; no component changes.

## Gates

If you changed any stylesheet: from `frontend/`, `npm ci`, `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build` (summary lines verbatim). Do not commit. Stop every
process you start, by PID. Nothing under `/tmp`.
