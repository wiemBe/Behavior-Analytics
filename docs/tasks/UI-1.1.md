# Task UI-1.1 — redesign Phase 1: charcoal tokens and self-hosted Inter (UI-1.1 + UI-1.2)

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all.

The user approved the redesign plan on 2026-10-14, with the answers recorded as **D-11** below.
This task is the first code of the redesign. It combines plan tasks UI-1.1 (tokens) and UI-1.2
(Inter). **It changes colours, radii and the font, and nothing else.** No layout, no markup, no
component, no route.

## Binding

- [`docs/UI-REDESIGN-PLAN.md`](../UI-REDESIGN-PLAN.md):
  - UR.2.1, the mapping of every existing token;
  - UR.2.3, the new tokens;
  - UR.2.4, the severity scale for charts only;
  - UR.2.5, Inter, self-hosted;
  - UR.2.6, the chart palette;
  - UR.2.7, the map ramp, whose tokens are defined now and used later;
  - UR.2.8, the contrast table.
- `docs/UI_Suggestions/UI_Suggestion.md`, the palette table: the binding source of the values.
- `docs/PRODUCT-UX-GUIDELINES.md`. Read `frontend/src/app/tokens.css` and its header comment.

## D-11 — the user's decisions, which override the plan where they differ

1. **Keep the palette's original values exactly. Do not apply the plan's contrast deviations
   (UR.2.2).**
   - `--text-3` is `#73757B`, the palette's "Muted Text", **not** `#8C8E94`.
   - `--border-strong` is `#35363A`, the palette's only "Border" value, **not** `#6E7077`.
   - Do **not** add `--text-disabled`. Disabled text uses `--text-3`, as it does today.
   - `--chart-label` is `var(--text-3)`.
   - The tooltip border is `var(--border)`.
   - Every other value in UR.2.1 and UR.2.3 is unchanged.
2. Dark only. `color-scheme: dark` stays, and no light theme is added.
3. Text-only navigation, with no icons. That is out of scope here anyway.

Update the header comment of `tokens.css` so it states D-11: muted text is the palette's
`#73757B` by the user's decision, and it is below WCAG AA on every surface.

## Build

1. **`frontend/src/app/tokens.css`**:
   - every existing token gets its UR.2.1 value, with the D-11 overrides above. Keep every
     existing token name, because tests read them;
   - add the UR.2.3 tokens, except `--text-disabled`. These are `--sidebar-bg`, `--primary`,
     `--primary-hover`, `--link`, `--topbar-h`, `--chart-cat-1…5`, `--chart-other`,
     `--sev-critical/-high/-medium/-low/-info`, `--map-0…4`, `--widget-pad` and `--widget-gap`.
     Define them even where nothing uses them yet;
   - radii `--r-sm/-md/-lg` become 4/6/8 px. `--font` is Inter first, then today's stack;
   - `color-scheme: dark` stays.
2. **Inter, self-hosted (UR.2.5):**
   - Copy `inter-latin-wght-normal.woff2` and `inter-latin-ext-wght-normal.woff2` from
     `@fontsource-variable/inter@5.3.0` into `frontend/src/app/fonts/`, with the SIL OFL text as
     `OFL.txt`.
   - Fetch the package into a scratch directory **outside the repository** (for example
     `npm pack @fontsource-variable/inter@5.3.0`). **Do not add it to `package.json`.**
   - Check the two files' byte sizes against UR.2.5 and report them.
   - Add `frontend/src/app/fonts.css` with plain `@font-face` rules: the two `unicode-range`
     subsets, `font-weight: 100 900`, `font-display: swap`. Import it once from `globals.css`.
   - **No request may leave for a font, at build or at runtime.** Use no `next/font/google` and
     no external URL.
3. **The sidebar panel.** Where `globals.css` paints the sidebar (`.layout::before`, today
   `var(--surface)`), use `var(--sidebar-bg)`. That is the **only** `globals.css` change besides
   the `@import`, plus any change needed so nothing still references a removed value. Since no
   token is removed, nothing should need changing.
4. **Primary button.** Where the primary button fill uses `--info` today, it becomes
   `var(--primary)`, with hover `var(--primary-hover)` and text `var(--on-accent)` (UR.2.3). The
   focus ring stays `var(--focus)`, now neutral. Make this change wherever the rule lives
   (`globals.css` or `components/ui/controls.css`), and only to the fill tokens.
5. **Links.** Content links use `var(--link)` where they use `--info` or `--info-text` today.
   Change only the token reference.

## Tests

- **New `frontend/src/lib/contrast.test.ts`.** It reads `tokens.css`, computes WCAG 2.x relative
  luminance contrast, and asserts:
  - every UR.2.8 text pair is ≥ 4.5:1, **except** `--text-3`. Pin `--text-3 === "#73757B"`
    with a comment naming D-11, the user's decision of 2026-10-14 to keep the palette value
    below AA. Do not assert AA for it;
  - the status `*-text` pairs on surface and on their tints, `--on-accent` on `--primary`, and
    `--focus` on canvas, surface and elevated: each at its UR.2.8 threshold;
  - no `--chart-cat-*` equals `--ok`, `--warn` or `--crit`;
  - **no colour literal outside `tokens.css`.** Scan `globals.css`, `fonts.css` and
    `components/**/*.css` for `#hex` and `rgb(`.
- `frontend/src/app/shellFixes.test.ts`: the **one** assertion that pins `.layout::before` to
  `var(--surface)` becomes `var(--sidebar-bg)`. Keep its intent: the sidebar is painted on
  `.layout::before` with a token. Change nothing else there.
- `chartPalette.test.ts` must pass **unchanged**.
- Every other existing test must pass unchanged. If one fails, stop and report it; do not edit it.
- Break the contrast test on purpose (for example, set `--text` to a dark grey) and confirm it
  fails. Then restore (AGENTS.md §4).

## Screenshots (required for acceptance)

This is a colour, radius and font pass. Every route must look **intact**: the same layout, now in
charcoal.
- Capture **every route** listed in `docs/tasks/UI-R0.md` "Every route that exists today", plus
  `/admin/outage-policy` and `/fleet/catchall`, at **1440×900, 1280×800 and 1024×768**.
- Use headless Chrome (`/usr/bin/google-chrome`) against a deterministic fixture API, as
  `docs/UI-WORKSTREAM-COMPLETION.md` §4 describes. Earlier tasks' fixtures and capture scripts
  can be adapted.
- Keep every script and fixture **outside the repository**, under
  `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui11/`.
- Pick ports no other process uses, for example app 3811 and fixture 4811.
- For each capture, record whether `document.documentElement.scrollWidth === clientWidth`.
- Use the headless run's network log to show no request leaves the fixture host and the app
  origin, and in particular no font request goes to an external host.
- Also capture one page with Turkish text (a fixture source named `Güvenlik Duvarı İstanbul`, for
  example) to show `ş ğ İ ı` render in Inter.
- List the PNG paths in your report, grouped by route.

## Allowed files

- `frontend/src/app/tokens.css`, `frontend/src/app/globals.css` (as items 2–5 allow),
  `frontend/src/components/ui/controls.css` (item 4 only)
- `frontend/src/app/fonts/` (new: two woff2 files and `OFL.txt`), `frontend/src/app/fonts.css`
  (new)
- `frontend/src/lib/contrast.test.ts` (new), `frontend/src/app/shellFixes.test.ts` (the one
  assertion)
- `docs/tasks/UI-1.1.report.md` (new)

## Gates

From `frontend/`, with `export PATH=$HOME/.local/node/bin:$PATH`: `npm run lint`,
`npm run typecheck`, `npm test`, `npm run build`. All must pass. Report real counts.

Write `docs/tasks/UI-1.1.report.md` in the AGENTS.md §6 format, with the screenshot list. Do not
commit.
