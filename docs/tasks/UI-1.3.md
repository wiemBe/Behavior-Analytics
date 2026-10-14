# Task UI-1.3 — redesign Phase 1: sidebar navigation and shared components (UI-1.3 + UI-1.4)

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all: the user does
not want "AI slop". Read `docs/PRODUCT-UX-GUIDELINES.md` (normative) and
`frontend/src/app/tokens.css`. UI-1.1 has already set the charcoal tokens and Inter: **use the
tokens, add no literal.**

The user approved [`docs/UI-REDESIGN-PLAN.md`](../UI-REDESIGN-PLAN.md) with decision D-11
(`docs/PLANNER.md` §4):
- `/` is the Dashboard;
- **text-only navigation, no icons**;
- dark only;
- the palette's original values: `--text-3 #73757B`, `--border-strong #35363A`.

This task combines plan tasks **UI-1.3** (sidebar) and **UI-1.4** (shared components). It
restyles. **It changes no behaviour, no data and no route.**

## Part 1 — sidebar (UR.3.2)

- Regroup `frontend/src/lib/nav.ts` exactly as the UR.3.2 table says:
  - an unlabeled first group with **Dashboard** (`/`): the "SOC Overview" entry renamed, same
    route;
  - then **Behavior**, **Fleet** (including Catch-all senders, which is merged), **Detection**,
    **Operations** and **Administration**.
  - Every existing route stays reachable, and no entry is added for a route that does not exist.
- **Selection style:** the active item gets a `--surface-2` background, a 2 px left rule in
  `--neutral` and `--text`; hover is `--surface-3`. Never blue. `aria-current="page"` stays the
  semantic carrier.
- **Brand:** keep today's text and markup. The shell holds no heading element. Add no tagline,
  version or icon.
- **Not in this task:** the top-bar row of UR.3.1. It comes with its contents in UI-1.5. **Do not
  add an empty bar or a placeholder.** The layout stays two columns.
- Test edits, the only ones allowed in Part 1 (from UR.7 UI-1.3):
  - in `frontend/src/lib/nav.test.ts`, the `NAV` structure assertions are rewritten to the
    UR.3.2 groups, **with the same intents**:
    - the first group is unlabeled and leads;
    - the behavioral destinations are grouped first among the labeled groups;
    - the administration list equals today's five;
  - `links to no route that does not exist` stays unchanged, and gains `/reports` as a
    must-not-exist case;
  - in `frontend/src/components/shell/AppNav.test.tsx`, change only an assertion that names a group
    label the regroup removes.
  - List every edited assertion in your report: the old text, the new text, and why the intent is
    kept.

## Part 2 — shared components (UR.7 UI-1.4)

Restyle in CSS. **Never rename a class that a test pins** (UR.8.1 lists them: `num`, `crit`,
`perm`, `nowrap`, `cell-name`, `cell-end`, `cell-fold`, `code-block`, `ips-status`, `is-warn`,
`cron`, `sr-only`, `pill`, `health-seg`). New classes may be added.
- **Buttons:**
  - `button.action` (primary) is the neutral light fill UI-1.1 set (`--primary`, text
    `--on-accent`);
  - the secondary buttons are a charcoal surface with a `--border-strong` edge;
  - the disabled state stays visibly disabled; focus is `--focus` with a visible ring;
  - corners use the radii tokens.
- **Inputs, selects and text areas:**
  - `--surface-2` fill, a `--border-strong` edge (`#35363A` by D-11), `--text` and a `--focus`
    ring;
  - set **`accent-color`** for native radios and checkboxes to a neutral token (they render
    browser-default **blue** today, for example on `/admin/waf-sources/[id]`).
- **Pills:** the severity and state pills follow UR.2.4: `sevTone()` is unchanged, the words carry
  the difference, and there is no orange.
- **Tables:** the header in `--text-3` at `--fs-xs` and not uppercase; row separators in
  `--border`; hover in `--surface-3`; numbers right-aligned with tabular figures, as today.
- **Notices, banners, `LoadingState`, `ErrorState`, `RouteState`:** charcoal surfaces with their
  status tint and text tokens. The contracts are unchanged.
- **Cards and panels:** `--surface`, a `--border` hairline, the radii tokens, the one `--shadow`
  token at most. No nested-card look, no gradient, no glow.
- All existing tests pass with **no** edits in Part 2. If one fails, stop and report it.

## Screenshots (required for acceptance)

The shell is on every page, so capture **every route** at **1440×900, 1280×800 and 1024×768**:
the routes in `docs/tasks/UI-R0.md` plus `/admin/outage-policy` and `/fleet/catchall`.
- Use headless Chrome (`/usr/bin/google-chrome`) against a deterministic fixture API.
- Adapt UI-1.1's capture kit, read-only, from
  `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/`
  (`capture.py`, `fixture.mjs`). Copy it; do not edit it in place.
- Work in
  `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/`,
  with ports 3813 (app) and 4813 (fixture).
- Extend the fixture so these appear:
  - a disabled primary button;
  - a focused input: capture one with focus forced;
  - a checked radio;
  - every severity pill;
  - an error state;
  - an empty state.
- For each capture, record whether `document.documentElement.scrollWidth === clientWidth`.
- **Before you finish, open and look at** at least these PNGs yourself and say what you saw in the
  report: `/`, `/behavior`, `/anomalies/[id]`, `/fleet`, `/admin/waf-sources/[id]`, and
  `/admin/search-cadence`, each at 1024.
- List every PNG path, grouped by route. Stop every process you started.

## Allowed files

- `frontend/src/lib/nav.ts`, `frontend/src/lib/nav.test.ts`,
  `frontend/src/components/shell/AppNav.tsx`, `frontend/src/components/shell/AppNav.test.tsx` (Part 1
  edits only)
- `frontend/src/app/globals.css`, `frontend/src/components/ui/controls.css`
- `frontend/src/components/ui/*.tsx`, for markup only where a new class must be added
- `frontend/src/app/tokens.css`, **only** to add a token that Part 2 needs (for example an
  `accent-color` token), with its comment
- `docs/tasks/UI-1.3.report.md` (new)

## Gates

From `frontend/`, with `export PATH=$HOME/.local/node/bin:$PATH`: `npm run lint`,
`npm run typecheck`, `npm test`, `npm run build`. All must pass. Copy the summary lines verbatim
into the report.

`frontend/node_modules` is a symlink to the main checkout. Never install into it or replace it.
Run no state-changing git command. Write `docs/tasks/UI-1.3.report.md` in the AGENTS.md §6
format. Do not commit.
