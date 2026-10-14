# Task UI-1.5 — redesign Phase 1: the top bar (D-10: existing APIs only)

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all: the user does
not want "AI slop". Read `docs/PRODUCT-UX-GUIDELINES.md` (normative) and
`frontend/src/app/tokens.css`. UI-1.1 and UI-1.3 are merged: charcoal tokens, Inter, the grouped
text-only sidebar and the restyled controls. **Use the tokens and the existing components; add no
literal, no icon library and no new dependency.**

## Binding

- [`docs/UI-REDESIGN-PLAN.md`](../UI-REDESIGN-PLAN.md):
  - **UR.3.1**, the layout: the brand top-left over the sidebar column, the top bar to its right;
  - **UR.3.4**, the top bar, every element with its data;
  - UR.8.4, SSR and hydration;
  - UR.8.6, the 1024 viewport.
- Decision D-10: **existing APIs only.** D-11: text only, with no icons. The bell is a text or
  number control, not a glyph; if any glyph is used, it is a plain inline SVG with an accessible
  name. **No new backend endpoint.**

## Build

1. **Layout (UR.3.1).** Add the top-bar row with its contents, so there is never an empty bar:
   - the brand moves into the top-left cell, keeping today's text and markup;
   - the sidebar keeps its `.layout::before` painting with `--sidebar-bg`, and the active-item
     scroll from UI-1.3;
   - `--topbar-h` sizes the row;
   - the shell holds no heading element;
   - no page overflows at any of the three viewports.
2. **`TopBar`** (`frontend/src/components/shell/TopBar.tsx`, a client component). Build each part
   as its own component with its own test.
   - **`TopSearch`**, the UR.3.4 "Search":
     - the server-side lists: `GET /fleet/sources?q=&limit=5`, `GET /rules?search=&limit=5`,
       `GET /offenses?search=&limit=5`;
     - anomalies as a client filter over **one** bounded read, `GET
       /anomalies?active_only=true&limit=200`, cached for the session. The group is labelled
       "Active anomalies (first 200)";
     - a 250 ms debounce, the previous request aborted, at most 5 results per group;
     - a group that returns `403` is omitted and named once in a footer line ("Offenses not
       searched: your role cannot read them"), never shown as "no results";
     - the combobox pattern (`role="combobox"`, `aria-expanded`, `aria-activedescendant`, `Esc`
       closes). Results are links; there are no actions;
     - `Ctrl+K` / `⌘K` focuses it.
   - **`AutoRefresh`:**
     - a select with `Off`, `1 min`, `5 min`; the default is `Off` everywhere for now, since the
       Dashboard default of 5 min comes with Phase 2;
     - a refresh calls `router.refresh()`;
     - it shows the **time the browser last refreshed**, in UTC with the zone;
     - it pauses while the tab is hidden and while a dialog is open;
     - "data to …" appears only where a page supplies its data age. No page does yet, so leave the
       hook (a context the page can set) but show nothing until a page sets it. Do not invent an
       age.
   - **`AlertBell`:**
     - `GET /overview` → `alerts.open`, linking to `/alerts`;
     - the badge uses the `crit` tone only when the count is above 0;
     - a failed or forbidden read shows **no number** and the title "Alert count unavailable",
       **never `0`**;
     - the accessible name is "Open alerts: 3" or "Open alerts: unavailable".
   - **`ConnectionStatus`**, three named signals:
     1. `GET /health/ready` → "Backend and database" ok or "Backend unreachable";
     2. `GET /overview` → `instance_status` and `instance_version`, plus the age from
        `instance_last_health_check_at` (B-1.2). When that is `null`, say "never checked".
        Never hide that, and never imply freshness;
     3. `GET /admin/search-cadence` → `last_run.at` and `outcome` for `PHASE_A_VOLUME`. On `403`
        this signal is **omitted**, not shown as bad.
     - The control shows the worst of the signals as a word, and expands (a `<details>` or a
       disclosure button) to the lines.
     - `UNKNOWN` is neutral, never green.
     - Do not show "Flow Collectors" or "Event Processors": no data source exists.
   - **Not in this task:** the time-range control (it appears only on routes that register
     support, the first being the Phase 2 Dashboard) and any user menu.
3. **Data fetching.** These are browser reads through `frontend/src/lib/api.ts`. Add typed client
   functions only where missing.
   - **Check that a browser `GET` works from the app origin with the existing
     `NEXT_PUBLIC_API_BASE_URL` and CORS (UR.8.4).** Say how you checked in the report. If it does
     not work, stop and report it; do not change the backend.
   - Nothing reads `window` during render. Times that depend on "now" render after mount only.
   - StrictMode-safe: abort on unmount.

## Tests (Vitest; every state, AGENTS.md §3a)

- `TopSearch`:
  - debounce and abort;
  - the result groups;
  - a `403` group omitted and named in the footer;
  - an empty result says what is empty;
  - an error;
  - keyboard: `Ctrl+K`, the arrows, `Esc`;
  - the anomaly group labelled "first 200".
- `AutoRefresh`:
  - `Off` does nothing;
  - `1 min` calls `router.refresh` (fake timers);
  - it pauses on `visibilitychange`;
  - the last-refreshed time is in UTC.
- `AlertBell`: a count; zero, which shows no `crit` tone; a failure shows no number; a `403` shows
  no number.
- `ConnectionStatus`:
  - all ok;
  - the backend down;
  - the instance `UNREACHABLE`;
  - the instance `UNKNOWN`, which is neutral;
  - never checked;
  - the cadence signal omitted on `403`;
  - the worst-of rule.
- The shell: no heading; the brand is a link; the existing `AppNav` and `shellFixes` tests stay
  green **unchanged**. If one needs an edit, stop and report.
- Break the code on purpose for at least three guards (for example: show `0` on a failed bell;
  show the cadence signal as bad on `403`; drop the debounce). Confirm each fails a test and
  restore it.

## Screenshots (required for acceptance)

- Capture `/`, `/behavior`, `/fleet` and `/admin/search-cadence` at **1440×900, 1280×800 and
  1024×768**, with:
  - the search open with results;
  - the search with a `403` group;
  - the bell with a count, and the bell unavailable;
  - the status expanded in the all-ok, instance-unreachable and never-checked states.
- Use a deterministic fixture API. Adapt the UI-1.3 kit, copied, from
  `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/`.
  Work in `.../scratchpad/ui15/`, with ports 3815 (app) and 4815 (fixture).
- Record `scrollWidth === clientWidth` for each capture.
- **Open and look at** at least six PNGs yourself, including all three viewports of `/`, and say
  what you saw.
- List every PNG path. Stop every process you started.

## Allowed files

- `frontend/src/components/shell/TopBar.tsx`, `TopSearch.tsx`, `AutoRefresh.tsx`,
  `AlertBell.tsx`, `ConnectionStatus.tsx` (new, each with a `.test.tsx`)
- `frontend/src/app/layout.tsx` (mount and grid), `frontend/src/app/globals.css` (the shell and
  top-bar section), `frontend/src/lib/api.ts` (typed client functions only)
- `docs/tasks/UI-1.5.report.md` (new)

## Gates

From `frontend/`, with `export PATH=$HOME/.local/node/bin:$PATH`: `npm run lint`,
`npm run typecheck`, `npm test`, `npm run build`. All must pass. Copy the summary lines verbatim.

`frontend/node_modules` is a symlink to the main checkout. Never install into it or replace it.
Run no state-changing git command. Write `docs/tasks/UI-1.5.report.md` in the AGENTS.md §6
format. Do not commit.
