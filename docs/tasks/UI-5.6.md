# Task UI-5.6 — log sources, searches and alerts: review against the redesign and fix

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative: §4, §6, §8, §9, §10), `frontend/src/app/tokens.css`,
`frontend/src/components/ui/controls.css`, and in `docs/UI-REDESIGN-PLAN.md`: **UR.1** (its "Rule
for every entry" and the `/log-sources`, `/searches`, `/alerts` entries of **UR.1.2**), **UR.2**,
**UR.3.3**, **UR.7 Phase 5**, **UR.8.1** and **UR.8.6**. Read `docs/tasks/UI-5.4.md` (the method)
and **`docs/tasks/T-096.report.md`**: T-096 wrote the characterization tests these pages now have,
and recorded the defects it found as `it.todo`.

## What "restyle" means here

Phase 1 moved every page to the new tokens, typography, shell and shared components. These pages
were written against the old navy tokens and have not been looked at since. This task looks at
them closely, finds where they fall short of the redesign and the guidelines, and fixes that in
CSS and minimal markup, **plus the honesty defects T-096 recorded**. It changes no request, no
permission rule, and no operator action's behaviour.

Compare each page with the Home and the anomaly pages, which were built on the new system: the
same kind of thing (a table, a status pill, a time, an empty notice, a code panel) must look and
behave the same way. Inconsistency between pages is a defect.

## Routes

`/log-sources`, `/log-sources/[id]`, `/searches`, `/searches/[id]` (with a truncated execution and
a result trend with gaps), `/alerts`, `/alerts/[id]` (open, acknowledged, resolved; with and
without notifications, including a dead-lettered one).

## The defects T-096 recorded (fix each; turn its `it.todo` into a passing test)

- `/log-sources`: the subtitle says "0 sources" when the read failed; an unknown QRadar status is
  toned as critical (it must be neutral); the last-event time has no timezone.
- `/log-sources/[id]`: the last-event time has no timezone; the page uses **inline styles**
  (§3a bans them): move them to a stylesheet.
- `/alerts`: first- and last-seen times have no timezone.
- `/alerts/[id]`: opened and delivery times have no timezone.

Use the app's existing time formatter (the one the Home and anomaly pages use) so every time reads
the same way. Each `it.todo` becomes an `it(...)` asserting the correct behaviour; it must fail on
the old code (break-it by reverting your fix) and pass on the new.

## Method

1. **Capture first**, every route and state at 1440×900, 1280×800 and 1024×768, into
   `…/ui56/png/before/`.
2. **Review:** a defect list in the report (route, viewport, what is wrong, the rule, the fix),
   including the T-096 defects and at least: page overflow; the shared `PageHeader`; literals and
   inline styles; numbers (unit, tabular, right-aligned); times with a timezone; every state
   designed (loading, empty, error, and the honesty states UR.1.2 lists: `health_score` null,
   `health_breakdown` null, execution `truncated`, result-trend gaps); the stored AQL shown only
   in `CodePanel` (rule 4: never an input); the operator actions (`AlertActions`,
   `RunSearchButton`, `ScheduleControl`): labels, focus, disabled reasons, confirmation where an
   action changes state.
3. **Fix** within the allowed files. Pinned classes stay (UR.8.1): change their CSS, never rename
   them. Keep the DOM order the tests pin.
4. **Capture after** into `…/ui56/png/after/`. Look at every PNG; say per route what changed.

## Tests

- All existing tests stay green. The only existing-test edits allowed are turning T-096's
  `it.todo` lines in these six routes' `page.test.tsx` files into real tests. If any other fix
  needs a test edit, it is the wrong fix; if a test seems wrong, stop and report it.
- Add tests for markup you add. **Break-it** each new or converted test: confirm it fails, then
  restore.

## Screenshots and fixture

- Fixture API in `/home/efe/Documents/QRadar-Dash-work/ui56/fixture/` (outside the repository) with
  the **backend's real payload shapes** (read `backend/app/schemas/` for log sources, searches,
  executions, results and alerts; the T-096 test files show shapes in use).
- Ports 3148/8148/9148. Headless Chrome is `/usr/bin/google-chrome`.
- PNGs under `/home/efe/Documents/QRadar-Dash-work/ui56/png/{before,after}/`. Nothing under
  `/tmp`.

## Allowed files

- the six routes' page files, their `page.test.tsx` (only as above), and colocated stylesheets
- `frontend/src/components/searches/*`, `frontend/src/components/AlertActions.tsx`,
  `frontend/src/components/ResultTrend.tsx`, `frontend/src/components/RunSearchButton.tsx`, and
  their tests (new tests only)
- `frontend/src/app/tokens.css` (new tokens only, each justified in the report)
- `docs/tasks/UI-5.6.report.md` (new)

Not `globals.css`, not `components/ui/*`, not other pages: if a shared component is the cause,
list it as an open point.

## Gates

From `frontend/` with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache` (the `todo` count must drop by the number you converted),
`npm run build`. Copy each summary line verbatim into the report (AGENTS.md §6 format), with the
defect list, the before/after PNG lists and what changed per route. Do not commit. Stop every
process you start, by PID. Never `pkill -f` or `killall`.
