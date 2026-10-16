# Task UI-6.4 — cleanup: dead CSS, dead exports, literal check (Phase 6 QA)

Read `AGENTS.md` at the repository root first and follow it exactly (§1, §3a, §4). Then read the
UI-6.4 row of `docs/UI-REDESIGN-PLAN.md` **UR.7 Phase 6** and `frontend/src/app/tokens.css`.

Start after Phase 5 is merged. `echarts-for-react` is already gone (T-099).

## What to do

1. **Dead CSS.** For every selector in `frontend/src/**/*.css`, find whether any class it names is
   used: in TSX `className`, in `clsx`/template strings, or in tests. Dynamically built class
   names (`` `pill-${state}` ``) count as used for every value they can take; list them.
   - Report a table: file, selector, evidence it is unused.
   - Remove only the selectors you can prove unused.
   - A selector pinned by a CSS contract test (`shellFixes.test.ts`, `nowrapContract.test.ts` or
     similar) stays.
2. **Dead exports and files.** Find exported functions, components and types under
   `frontend/src/` that nothing imports, tests excepted.
   - Report them.
   - Remove a file or export only when nothing imports it and no test covers it as a contract.
   - **The orchestrator authorises deleting these files.** List each one.
   - Never delete or edit a test to make a removal pass. If a test imports it, it stays.
3. **Literals.**
   - Confirm that `globals.css` and every other stylesheet outside `tokens.css` contain zero
     colour literals (hex, `rgb(`, `hsl(`, named colours other than `transparent`,
     `currentColor`, `inherit`).
   - Count spacing and type-size literals (px or rem values that are not tokens) per file.
   - Fix colour literals by mapping each to its existing token (no new token unless justified).
   - Report the spacing and type counts; do not mass-convert them.
   - Add one contract test that pins "no colour literal outside `tokens.css`", if none exists.
4. **Prove nothing visible changed.**
   - Capture the 12 most-used routes at 1440×900 and 1024×768 before and after: `/`,
     `/behavior`, `/behavior/sources/[id]`, `/anomalies`, `/anomalies/[id]`, `/behavior/waf`,
     `/fleet`, `/log-sources`, `/alerts`, `/rules`, `/rules/statistics`, `/admin`.
   - Pixel-diff them. A diff above 0 % on any route is a regression to explain or undo, except
     the clock text in the top bar (mask it).
   - Reuse `/home/efe/Documents/QRadar-Dash-work/t111/capture.mjs` (copy it).

## Allowed files

- any `frontend/src/**/*.css`, for removals and colour-literal fixes only
- `frontend/src/**/*.ts(x)` files that are dead per item 2 (deletion), or that only re-export a
  removed symbol
- one new contract test
- `docs/tasks/UI-6.4.report.md` (new)

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim. Ports 3164/8164/9164.
Scratch goes under `/home/efe/Documents/QRadar-Dash-work/ui64/`, never `/tmp`. Stop processes by
PID. Do not commit.
