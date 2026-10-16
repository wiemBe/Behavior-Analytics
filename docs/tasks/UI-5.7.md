# Task UI-5.7 — the administration pages: review against the redesign and fix

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative: §4, §6, §8, §9, §10), `frontend/src/app/tokens.css`,
`frontend/src/components/ui/controls.css`, and in `docs/UI-REDESIGN-PLAN.md`: **UR.1** (its "Rule
for every entry" and **UR.1.3 Administration**), **UR.2** (tokens, severity scale, typography),
**UR.3.3** (page header), **UR.7 Phase 5**, **UR.8.1** (test churn) and **UR.8.6** (1024×768).

## What "restyle" means here

Phase 1 already moved every page to the new tokens, typography, shell and shared components
(UI-1.1 to UI-1.5). These pages were written against the old navy tokens and have not been looked
at since. This task **looks at them, finds where they fall short of the redesign and the
guidelines, and fixes that in CSS and minimal markup.** It changes no behaviour: no request, no
permission rule, no validation rule, no copy that a test asserts.

## Routes

`/admin`, `/admin/search-cadence`, `/admin/classification-rules`, `/admin/outage-policy`,
`/admin/waf-sources`, `/admin/waf-sources/[logSourceId]`, `/admin/ips-sources`,
`/admin/ips-sources/[logSourceId]` (eight routes; the two detail routes each with a profile in
every validation state the page distinguishes).

## Method

1. **Capture first.** Build a deterministic fixture API (below), capture every route at 1440×900,
   1280×800 and 1024×768 **before any change**, into `…/ui57/png/before/`.
2. **Review.** Write a defect list into the report, one row per defect: route, viewport, what is
   wrong, which rule it breaks (§3a item or guideline section), and the fix. Look at least for:
   - page overflow at any of the three viewports (§9), and tables that overflow their container
     instead of scrolling inside it;
   - a header that is not the shared `PageHeader` (UR.3.3), or a description that restates the
     heading or is stale (`/admin` says "Further sections arrive in Phase 4": fix that copy; the
     sections exist);
   - colour, spacing or type literals outside `tokens.css`; inline styles; leftover navy-era
     classes or values;
   - numbers without a unit, not tabular, or not right-aligned in tables; times without a
     timezone;
   - states not designed: loading, empty (says what is empty and why), error (what failed, what
     the analyst can do), and the honesty states UR.1.3 lists for each route (for example
     `last_run` null = "has not run since the upgrade"; validation `UNAVAILABLE` and `FAILED`
     distinct);
   - forms: label association, error text tied to its field (`aria-describedby`), focus order,
     visible focus, buttons that say what they do; primary actions neutral as UI-1.4 set;
   - cards nested in cards, decorative shadows, a row of identical KPI cards.
3. **Fix** each defect within the allowed files. Pinned classes stay (UR.8.1: `perm`, `nowrap`,
   `crit`, `cell-end`, `num`, `is-warn`, `ips-status` and any other class a test asserts): change
   their CSS, never rename them. Keep the DOM order the page tests pin.
4. **Capture after**, same routes and viewports, into `…/ui57/png/after/`. Look at every PNG,
   before and after, and say in the report what changed on each route.

## Tests

- All existing tests stay green **with no edits**. If a fix needs a test edit, it is the wrong fix;
  if you believe a test is wrong, stop and report it.
- Each route already has a page test (UR.1.3 "none: pinned"). Add tests only for markup you add
  (for example an `aria-describedby` link, a designed empty state that did not exist).
- **Break-it** for each added test: confirm it fails, then restore.

## Screenshots and fixture

- Fixture API in `/home/efe/Documents/QRadar-Dash-work/ui57/fixture/` (outside the repository).
  It serves the routes UR.1.3 lists for these pages with the **backend's real payload shapes**
  (read the Pydantic schemas in `backend/app/schemas/` for search cadence, classification rules,
  outage policy, WAF and IPS profiles; the component `fixtures.ts` files show shapes the tests
  already use). Include: a cadence family that has never run; a classification rule list and an
  empty one; an outage policy with class and source overrides; WAF and IPS sources with profiles
  `VALID`, `FAILED`, `UNAVAILABLE` and none; a profile history.
- Ports 3144/8144/9144. Headless Chrome is `/usr/bin/google-chrome`. A copy of
  `/home/efe/Documents/QRadar-Dash-work/ui33/verify.mjs` may help measure overflow; quote any
  measurement you rely on.
- PNGs under `/home/efe/Documents/QRadar-Dash-work/ui57/png/{before,after}/`. Nothing under
  `/tmp`.

## Allowed files

- `frontend/src/app/admin/**` (pages, their components, their CSS)
- `frontend/src/components/cadence/*`, `frontend/src/components/fieldmapping/*`
- new component-colocated stylesheets beside those files
- `frontend/src/app/tokens.css` (new tokens only, each justified in the report)
- `docs/tasks/UI-5.7.report.md` (new)

Not `globals.css`, not `components/ui/*`: if a shared component is the cause, list it in the
report as an open point for the orchestrator.

## Gates

From `frontend/` with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim into the report
(AGENTS.md §6 format), with the defect list, the before/after PNG lists and what changed per
route. Do not commit. Stop every process you start, by PID. Never `pkill -f` or `killall`.
