# Task UI-6.5 — bring `PRODUCT-UX-GUIDELINES.md` up to the shipped redesign

Read `AGENTS.md` at the repository root first and follow it exactly. This is a **documentation
task**: you change no code, no test and no CSS.

## Why

`docs/PRODUCT-UX-GUIDELINES.md` is normative, and it says of itself "This document describes what
is implemented today". It was written before the UI redesign (`docs/UI-REDESIGN-PLAN.md`, Phases
1–6, now shipped on this branch). It does not know the `/` Dashboard, the widget system, the top
bar, the charcoal tokens, the chart palette, or the rulings the orchestrator made on the plan's
collisions with the rules (UR.9) and the user's decision D-11. Agents read this file before every
UI change, so it must match the product.

## Sources

- `docs/UI-REDESIGN-PLAN.md`: **UR.9** (the 17 collision rulings; C11 is superseded by D-11),
  UR.2 (tokens, palette, severity scale, contrast), UR.3 (shell, navigation, top bar), UR.4 and
  UR.5 (Dashboard and widget system).
- `docs/PLANNER.md` §4, decision **D-11**: `/` is the widget Dashboard and `/behavior` stays the
  behavioral triage page; navigation is text only, no icons; dark only, no light mode; the
  palette keeps `--text-3 = #73757B` and `--border-strong = #35363A` although `#73757B` is below
  WCAG AA, as the user's explicit exception for these two values only.
- The code, which wins over every document: `frontend/src/app/tokens.css`, `globals.css`,
  `frontend/src/components/` (`shell/`, `home/`, `ui/`, the widget registry and grid), `lib/nav.ts`,
  `lib/format*`, and the contract tests (`*Contract.test.ts`, `colourLiterals.test.ts`, the
  widget registry tests).
- The QA reports `docs/tasks/UI-6.1.report.md` to `UI-6.4.report.md`, `UI-6.6`, `UI-6.7`, for
  what the QA phase measured and which rules it found enforced.

## What to write

Edit `docs/PRODUCT-UX-GUIDELINES.md` in its own voice (short normative sentences, **must** /
**should** / **may** as its preamble defines them, no marketing words, no emoji). Keep every
existing rule unless the code proves it is no longer true; if you change or drop one, say so in
the report with the evidence.

1. **UR.9 rulings.** Write each ruling that is implemented as a rule in the section where an
   agent will look for it (for example C1 under §2/§3 and `StatCard`, C4 under §10 times, C7 and
   C10 in a new tokens/colour section, C12 and C13 with the severity scale). **Verify each one
   against the code before writing it** and cite the file in the report. A ruling the code does
   not implement is **not** written as a rule; list it in the report as a gap.
2. **D-11.** Dark only; text-only navigation; the two palette values and the explicit,
   bounded AA exception (state that it covers exactly these two values and nothing else).
3. **§3 Information architecture.** Add a `/` (Dashboard) entry: the question it answers, what
   it carries, what it must not become (C3: behavioral widgets get the larger area; supporting
   capabilities appear at lower weight). Add one short entry per other shipped route group
   (fleet, WAF, IPS, searches, alerts, admin…), each with the question it answers. Only routes
   that exist under `frontend/src/app/`.
4. **A tokens and colour section** (new, numbered where it fits): status colour is reserved for
   status; categorical charts use `--chart-cat-*`; the map's one-hue ramp; solid alpha fills,
   no gradients; one shadow token; no colour, spacing or type literal outside `tokens.css`
   (cite the contract test that enforces it).
5. **§8 Component contracts.** Add the shared components the redesign introduced that an agent
   must reuse (shell, top bar, page header changes, the widget frame and its states, the grid,
   range selectors…). For each, state the invariant its code and tests hold. Use the real
   component names; do not describe a component that does not exist.
6. **§9 / §10.** Add what the QA phase made true (for example interactive target sizes, if
   UI-6.2 or a later task enforced them; the dashboard's off-screen chart deferral from UI-6.7,
   if it belongs in a contract). Only what the code enforces.
7. **§12 checklist.** Add the checks the new rules need (one line each).

Do not edit `AGENTS.md`, `docs/PLANNER.md` or `docs/UI-REDESIGN-PLAN.md`; the orchestrator updates
`AGENTS.md` §3a from your report.

## Report

`docs/tasks/UI-6.5.report.md`, per AGENTS.md §6, plus:
- a table: each of C1–C17 and D-11 → written / not written, the section, and the file that
  proves it is implemented (or why it is a gap);
- every existing rule you changed or removed, with the evidence;
- **the exact text you propose for `AGENTS.md` §3a** (a short diff: which bullets change, which
  are added), for the orchestrator to apply.

## Gates

None of the code gates apply (no code changes). Run `git diff --stat` and show that only the two
allowed files changed.

## Allowed files

`docs/PRODUCT-UX-GUIDELINES.md`, `docs/tasks/UI-6.5.report.md` (new). Do not commit. Nothing
under `/tmp`.
