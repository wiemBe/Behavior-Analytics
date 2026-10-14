# Task UI-3.2 — drag, resize and keyboard steppers in customize mode (`react-grid-layout`)

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a (the frontend quality
bar) above all**. Then read `docs/PRODUCT-UX-GUIDELINES.md` (normative),
`frontend/src/app/tokens.css`, and what UI-3.1 built: `frontend/src/widgets/*` (`DashboardView`,
`layout.ts`, `dashboard.css`, the registry's `size` bounds) and `docs/tasks/UI-3.1.report.md`.

## Binding design

`docs/UI-REDESIGN-PLAN.md` **UR.5.2** (the library, its version, React 19, SSR, the CSS rule) and
**UR.5.3** (interactions: drag and resize in customize mode only; the keyboard alternative; editing
only at 12 columns; the breakpoints are 12 at ≥ 900 px, 6 from 600 to 899, 1 below).

## Build

1. **Dependency.** `react-grid-layout@2.2.4`, pinned exactly (`--save-exact`), as UR.5.2 proposes.
   Check `npm view react-grid-layout@2.2.4` first. If 2.2.4 fails acceptance item 1 below, try
   `2.3.0`, then `1.5.4` with the legacy import (UR.5.2 order), and report what failed. This
   worktree has **its own** `node_modules` (not the shared symlink): run `npm ci` once, then the
   install, so `package.json` and `package-lock.json` change and nothing else.
2. **Where it runs.** The read view stays as UI-3.1 built it (the CSS grid; server-rendered; no
   `react-grid-layout`). **Customize mode at 12 columns** renders the placed widgets through
   `react-grid-layout`, loaded with `next/dynamic` (`ssr: false`) only when customize mode opens,
   with `useContainerWidth({ measureBeforeMount: true })` (not the v1
   `WidthProvider`). The edit grid uses **the same geometry** as the read grid: 12 columns, row
   height and margins read from `--dash-row` and `--dash-gap` (computed once on mount, in px), so a
   widget does not jump when customize mode opens or closes.
3. **Drag and resize.** The widget's title row is the drag handle (`draggableHandle`); links,
   buttons and inputs never start a drag (`draggableCancel`). Resizing from the bottom-right corner
   only. Each widget's `minW`, `minH`, `maxW`, `maxH` come from its registry `size`. Vertical
   compaction on (the gap UI-3.1 leaves after Remove closes). The layout state is the same layout
   v1 the view already holds; "Done" leaves customize mode with the new positions, still in memory
   (**nothing is saved**; the session-only line stays).
4. **Keyboard alternative (§10; UR.5.3).** Drag is pointer-only, so in customize mode each widget
   offers Move (left, right, up, down by one cell) and Resize (wider, narrower, taller, shorter by
   one cell) buttons, named for the widget ("Move Total EPS left"), keyboard reachable, disabled at
   their bounds. They go through pure, tested functions in `layout.ts` (`moveWidget`,
   `resizeWidget`) that respect the bounds and the 12 columns and resolve collisions the same way
   the drag does (compaction). Keep the chrome compact: the steppers must not push a widget's
   content out of its box at the template sizes (a small toolbar that appears in customize mode, or
   one "Arrange" button per widget that reveals them; choose and justify).
5. **Styling.** Do not import `react-grid-layout/css/styles.css` as is (UR.5.2): rewrite only the
   rules it needs (positioning, the placeholder, the resize handle) in `dashboard.css` with tokens,
   no literals, and **no animation outside an active drag** (`transition: none` otherwise). The
   placeholder and the handle follow the palette (no glow, no gradient, no decorative shadow).
6. **Inline style.** `react-grid-layout` positions items with inline transforms; that is the
   library's mechanism and is accepted, like UI-3.1's `--dg-*` numbers. Add no other inline style.

## Acceptance

1. **Drag, resize and the keyboard steppers verified in headless Chrome** (`/usr/bin/google-chrome`),
   on `/` in customize mode at 1440×900: a script outside the repository drives real pointer
   events through the DevTools protocol (`Input.dispatchMouseEvent`) to drag one widget to a new
   cell and to resize another, and presses the steppers with the keyboard; after each step it reads
   back the layout (expose each item's `x`, `y`, `w`, `h` as `data-*` attributes) and asserts the
   change. Quote the script and its output in the report.
2. Opening and closing customize mode moves no widget by more than 1 px (measure it).
3. `react-grid-layout` is not in the `/` read-mode bundle: show it from `next build` output or the
   network log, and report the client bundle cost of the customize chunk (UR.5.2).
4. No hydration warning in the browser console on `/` (read mode) or when entering customize mode.
5. At 1280×800 customize works the same; at 1024×768 the page still says "Widen the window to
   customize" and shows the read view unchanged.

## Tests (Vitest)

`moveWidget` and `resizeWidget` (each direction, each bound, collisions, compaction, `x + w ≤ 12`);
the steppers render, are named, and disable at bounds; the edit grid is not mounted in read mode
and not below 12 columns; Done keeps the new layout in memory. Every existing test stays green;
list any existing-test edit with its reason. Break at least five guards on purpose, confirm a test
fails, restore, list them.

## Screenshots (required)

Fixture API outside the repository: copy
`/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/a16a3a57-0bb1-488e-a4d5-dadbc9e16dfd/scratchpad/ui31/`
to `.../scratchpad/ui32/`; ports **3132** (Next), **8132** (fixture), Chrome debugging **9132**.
Customize mode at 1440×900 and 1280×800; one mid-drag capture showing the placeholder; the Home
after a drag and after a resize (customize closed); the read view at 1024×768. Look at every PNG
yourself and list them with what each shows.

## Allowed files

- `frontend/package.json`, `frontend/package-lock.json` (the one dependency)
- `frontend/src/widgets/*` (`Grid.tsx` new, `DashboardView.tsx`, `dashboard.css`, `layout.ts`, and
  their tests)
- `frontend/src/components/home/WidgetFrame.tsx` only if the drag handle or the steppers need a
  hook in the frame (say why)
- `frontend/src/app/tokens.css` (new tokens only)
- `docs/tasks/UI-3.2.report.md` (new)

Task UI-3.5a is adding widgets to the registry and fixing a catalog colour in parallel: do not
touch `registry.ts` or `Catalog.tsx`. Never `pkill -f` or `killall`; stop only the processes you
started, by PID.

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npm test`, `npm run build`. Copy each summary line verbatim into `docs/tasks/UI-3.2.report.md`
(AGENTS.md §6 format), with the PNG list and the verification script's output. Do not commit.
