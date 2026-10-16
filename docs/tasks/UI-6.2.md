# Task UI-6.2 — accessibility pass (Phase 6 QA)

Read `AGENTS.md` at the repository root first and follow it exactly (§3a). Then read
`docs/PRODUCT-UX-GUIDELINES.md` §10 (accessibility), `docs/UI-REDESIGN-PLAN.md` **UR.2.8** (the
contrast test), **UR.7 Phase 6** (the UI-6.2 row), and decision **D-11** in `docs/PLANNER.md` §4:
the user chose to keep `--text-3 #73757B` and `--border-strong #35363A` even though they are
below WCAG AA. **Do not change those two values and do not report them as defects**; record them
as the user's accepted exception.

Start after Phase 5 is merged. This task does not claim WCAG conformance (no full audit is run);
it fixes concrete failures and reports the rest.

## What to check (each with a method you can repeat, recorded in the report)

1. **Keyboard walk**, with a scripted headless-Chrome session (Tab / Shift+Tab / Enter / Escape /
   arrows), of: the shell (sidebar, top-bar search, refresh, alerts, status), the Dashboard's
   switcher and menu dialogs (UI-4.1/4.2), customize mode (add from the catalog, move and resize
   a widget with the keyboard, configure, done/discard), the anomaly list filters and the
   investigation page tabs, `AlertActions` (including the Resolve confirmation), and the admin
   editors (WAF/IPS profile editors, outage policy, classification rules). For each: focus order,
   visible focus ring, no trap, Escape closes dialogs and returns focus to the opener.
2. **Names and roles:** every interactive element has an accessible name; every table has a
   caption or `aria-label`; regions that scroll (`TableScroll`) are labelled and focusable; charts
   have a text equivalent (the summary or a named image); `role="alert"` / `role="status"` are
   used for errors and results.
3. **Contrast:** run the existing contrast test (UR.2.8) and list any token pair below AA other
   than the two D-11 values.
4. **Target size:** controls under 24×24 px (WCAG 2.5.8), measured on the live page. T-098 owns
   the widget frame's Expand link; list it but do not fix it here.
5. **Motion:** `prefers-reduced-motion` respected by every transition.

## Fix

Fix what is local and clear (a missing `aria-label`, a missing focus return, a missing caption)
in the component that owns it, with a test (Testing Library roles and names; `userEvent` for focus
order) and a break-it. Anything that changes layout or a shared component's API is a finding for
the orchestrator, not a fix.

## Allowed files

- components and pages with a local accessibility defect, the smallest change that fixes it, each
  listed in the report
- new tests for each fix
- `docs/tasks/UI-6.2.report.md` (new)

No change to `tokens.css` colour values (D-11). No existing test edited.

## Screenshots

Capture focus-visible states for the dialogs and customize mode at 1440×900 and 1024×768 into
`/home/efe/Documents/QRadar-Dash-work/ui62/png/` (ports 3162/8162/9162; fixtures copied from the
newest task folders). Open them. Nothing under `/tmp`.

## Gates

From `frontend/`: `npm ci`, `npm run lint`, `npm run typecheck`, `npx vitest run --no-cache`,
`npm run build`, summary lines verbatim (AGENTS.md §6). Do not commit. Stop every process you
start, by PID.
