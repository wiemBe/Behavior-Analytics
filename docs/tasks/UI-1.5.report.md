# UI-1.5 report: the top bar

Branch `agent/ui15`. Nothing committed. No backend file touched.

## 1. Files changed

- `frontend/src/components/shell/TopBar.tsx` (new): client component. Owns the shared reads (`/health/ready`, `/overview`, `/admin/search-cadence`), aborts them on unmount, repeats them on each auto-refresh, and exports the `Loaded<T>` type.
- `frontend/src/components/shell/TopSearch.tsx` (new): the combobox search.
- `frontend/src/components/shell/AutoRefresh.tsx` (new): the refresh select, the last-refreshed time, and the `DataAgeProvider` / `useReportDataAge` hook for a page's data age.
- `frontend/src/components/shell/AlertBell.tsx` (new): open-alert count link.
- `frontend/src/components/shell/ConnectionStatus.tsx` (new): three signals and the worst-of word.
- `TopSearch.test.tsx`, `AutoRefresh.test.tsx`, `AlertBell.test.tsx`, `ConnectionStatus.test.tsx`, `TopBar.test.tsx` (new): one per component; `TopBar.test.tsx` also covers the shell markup.
- `frontend/src/app/layout.tsx`: the brand moves into a `<header class="shell-head">` with `TopBar`; `.layout` (sidebar and main) follows; both are wrapped in `DataAgeProvider`. The brand text and markup are unchanged.
- `frontend/src/app/globals.css`: the shell and top-bar section only (`.shell-head`, `.layout`, `.sidebar` offset, `.brand`, new `.topbar*` and `.topsearch*` rules, and the 1200 px and 860 px overrides).
- `frontend/src/lib/api.ts`: appended `HealthReady` and `shellApi`, typed read functions that take an `AbortSignal`. No existing function changed.
- `docs/tasks/UI-1.5.report.md` (this file).

## 2. What I did

**Layout (UR.3.1).**
- `.shell-head` is a sticky 2-column grid (`--sidebar-w`, then `1fr`) of height `--topbar-h`. It holds the brand, painted with `--sidebar-bg`, and the top bar.
- It is a sibling above `.layout`, not a row inside it. A sticky grid item is bounded by its own grid area, so a row-1 top bar would scroll away. The brand-over-sidebar column and the top bar to its right are as UR.3.1 draws them.
- `.layout::before` still paints the sidebar with `--sidebar-bg`.
- The sidebar stays sticky, now at `top: var(--topbar-h)` with height `100vh` minus the bar, and still scrolls to the active item (AppNav untouched).
- The shell has no heading element.
- At 860 px and below the header un-sticks and stacks, below the supported floor.

**Top bar.** All of the following is built to UI-1.5.md. Choices I made where it was silent:
- **TopSearch**
  - Minimum 2 characters, with a hint line below that.
  - Results are `role="option"` anchors. Click or Enter calls `router.push`; a modified click keeps the browser default.
  - A group whose read failed (not 403) says "X could not be searched: the request failed. Try again." in place.
  - "No match." appears per group only when some other group has hits. When nothing matches anywhere, one status line says what was searched.
  - The anomaly read is cached in a ref for the component's life, and a failed read is not cached.
  - The previous query's results are cleared while a new search runs.
  - Typing aborts the three list reads. The anomaly read has its own lifetime signal, so typing does not abort the cache.
- **AutoRefresh**
  - Default is `Off`.
  - It calls `router.refresh()`, and also tells `TopBar` to re-read the bar's own data.
  - Pauses on `visibilitychange` (the interval is cleared while hidden).
  - Skips a tick while `dialog[open]` or `[aria-modal="true"]` exists.
  - Shows `Refreshed HH:MM:SS UTC`. The clock is null until mount, so nothing "now"-dependent renders on the server.
  - `Data to ...` shows only when a page calls `useReportDataAge`. No page does, so none is shown.
- **AlertBell**
  - A text "Alerts" plus a `pill`. The `crit` tone appears only for a count above 0.
  - A failed or forbidden read shows an em dash and the title "Alert count unavailable", never 0.
  - Names: "Open alerts: N", "Open alerts: unavailable", and "Open alerts: loading" for the first read.
- **ConnectionStatus**
  - Instance tone: HEALTHY ok, DEGRADED warn, UNREACHABLE crit, UNKNOWN or any other value neutral.
  - `instance_last_health_check_at === null` shows "never checked", and the tone is forced neutral whatever the stored status: nothing established it, and the plan forbids implying freshness. An absent field (older API) says "check time not reported".
  - Worst-of order: crit, warn, neutral, ok. An unknown signal therefore stops the control reading Connected.
  - A 403 on `/overview` or `/admin/search-cadence` omits the instance or collection signal. A non-403 failure is neutral "Unavailable".
  - The collection signal reads the `PHASE_A_VOLUME` row via the existing `outcomeInfo`.
  - No Flow Collectors or Event Processors.
  - Not built: the time-range control and the user menu.

**Browser GET check (UR.8.4).**
- `client.request()` uses `NEXT_PUBLIC_API_BASE_URL` in the browser (`.env.example`: `http://localhost:8000/api/v1`) and sends `Content-Type: application/json` on GETs, which triggers a preflight.
- I exercised the real backend app in process (`create_app()` under `TestClient`, backend venv, no DB, no backend file changed):
  - Origin `http://localhost:3000`: preflight 200 with `Access-Control-Allow-Origin: http://localhost:3000`, and the GET carries the same header. `Content-Type` is in the allowed headers.
  - Origin `http://localhost:3815`: preflight 400 with no ACAO header.
- So browser GETs work from the dev origin `localhost:3000` with the default base URL. `backend/app/main.py` allows only that origin outside production and none in production.
- A production deployment needs the SPA same-origin behind the reverse proxy, with `NEXT_PUBLIC_API_BASE_URL` pointing at that origin. It is inlined at build time. This is an open point, not a failure of this task.
- The screenshot fixture answers `Access-Control-Allow-Origin: *` and the `OPTIONS` preflight. Every one of the 84 captures had zero non-abort failed requests. `ERR_ABORTED` entries come from route prefetch and from my own search aborts, and were filtered out.

## 3. Gates

All run from `frontend/` with `PATH=$HOME/.local/node/bin:$PATH`, after the final edit:
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, no output, exit 0
- `npm test`: `Test Files  103 passed (103)` / `Tests  1666 passed (1666)`
- `npm run build`: `✓ Compiled successfully in 5.4s`
- `npm test -- src/components/shell src/app/shellFixes.test.ts`: `Test Files  7 passed (7)` / `Tests  77 passed (77)`. This includes `✓ src/app/shellFixes.test.ts (5 tests)` and `✓ src/components/shell/AppNav.test.tsx (12 tests)`, both unmodified.
- No state-changing git command was run. `git status` shows only the 3 modified and 10 new frontend files, plus this report.

**Break-it checks** (each edit made, the matching test file run, then the file restored from a copy):
- `AlertBell`: a failed read returns 0 instead of null. Result: 3 tests failed (error, forbidden and pending states).
- `ConnectionStatus`: a 403 cadence read shown as a `crit` "Forbidden" signal. Result: the "omits the collection signal on 403" test failed.
- `TopSearch`: `DEBOUNCE_MS = 0`. Result: 2 tests failed (the 250 ms boundary, and keystroke collapsing).
- `AutoRefresh`: the `!visible` pause guard removed. Result: the hidden-tab test failed.
- `ConnectionStatus`: the never-checked neutral override removed. Result: the "never checked" test failed.

## 4. Screenshots

The kit was copied from the UI-1.3 scratchpad to `.../scratchpad/ui15/`, with `fixture.mjs` and a new `capture15.py`. The UI-1.3 kit is unmodified.
- The fixture runs on 4815 and the app (production build with the fixture base URL) on 3815.
- The fixture adds `/health/ready`, switchable modes (instance unreachable, never checked, overview 500, offenses search 403, cadence 403), and `q` or `search` filtering on the three list endpoints.
- Chrome used remote-debugging port 9815.
- All processes were stopped; no listener on 3815, 4815 or 9815 remains.

Scenarios, each at 1440x900, 1280x800 and 1024x768 on `/`, `/behavior`, `/fleet` and `/admin/search-cadence` (7 x 4 x 3 = 84 PNGs): search with results (query "in"), search with the offenses 403, bell with a count, bell unavailable, and the status expanded in the all-ok, instance-unreachable and never-checked states.

**Overflow.** For every one of the 84 captures, `document.documentElement.scrollWidth === clientWidth` and `.topbar` `scrollWidth <= clientWidth`:
- 1440x900: 1440 = 1440
- 1280x800: 1280 = 1280
- 1024x768: 1024 = 1024

The shell header measured 56 px in all captures. (A "headings in header/aside" probe returned 1 on pages whose own `PageHeader` is a `<header>` holding the page `<h1>`; the shell itself has none, as `TopBar.test.tsx` asserts.)

**What I saw in the PNGs I opened (9):**
- `bell-count/root-1440x900.png`
  - Brand top-left over the sidebar column, search centred, then `Refresh [Off] Refreshed 09:12:15 UTC`, `Alerts (4)` with a red pill, and `Status [Connected]` with a green pill. 56 px bar, no empty gap.
  - The sidebar nav fits in 900 px with Dashboard active.
- `bell-count/root-1280x800.png`: same layout, search slightly narrower, nothing clipped.
- `bell-count/root-1024x768.png`
  - The sidebar is 196 px. The brand subtitle wraps to two lines but stays inside the 56 px cell.
  - The search shrinks and the placeholder ends in an ellipsis ("Search sources, rules, offenses, an..."). The refresh select keeps its width, and the bell and status fit with no page overflow.
- `search-results/root-1280x800.png` (earlier build, same layout)
  - The panel lists Sources (3), Rules (4), Offenses (3) and the "ACTIVE ANOMALIES (FIRST 200)" group, each with a right-aligned muted subline. The panel scrolls inside itself.
  - A fixture quirk: the "active" group shows a RESOLVED anomaly, because the fixture ignores `active_only`.
- `search-403/behavior-1280x800.png`: no Offenses group appears. The footer reads "Offenses not searched: your role cannot read them". The other groups show normally.
- `status-unreachable/root-1440x900.png`
  - The control reads `Problem` in red.
  - The list shows Backend and database OK, QRadar instance `Unreachable` (red) with "7.5.0 UP9 . checked 10-14 08:31 UTC, 41m ago", and Collection Complete (green).
- `status-never-checked/fleet-1024x768.png`
  - The control reads `Unknown` in neutral grey, with the instance `Unknown` (neutral) and "7.5.0 UP9 . never checked".
  - The fleet page beneath is unaffected.
- `bell-unavailable/admin_search-cadence-1024x768.png`: `Alerts (--)` with an em dash and no number. The control reads `Unknown`, because the instance signal is "Unavailable".
- Not individually opened: the remaining 75 PNGs. Their overflow numbers and the state strings (bell label, status label, footer, rows) were checked programmatically from `results.json` instead.

PNG list: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui15/pnglist.txt` (84 lines). The pattern is `.../scratchpad/ui15/png/<scenario>/<route>-<W>x<H>.png`.
- Scenarios: `search-results`, `search-403`, `bell-count`, `bell-unavailable`, `status-ok`, `status-unreachable`, `status-never-checked`.
- Routes: `root`, `behavior`, `fleet`, `admin_search-cadence`.
- Sizes: `1440x900`, `1280x800`, `1024x768`.
- Example: `.../scratchpad/ui15/png/status-ok/behavior-1280x800.png`.

Machine results: `.../scratchpad/ui15/results.json`.

## 5. Open points

- **Production CORS and base URL.** See the browser GET check above. `NEXT_PUBLIC_API_BASE_URL` is build-time and the backend allows only `localhost:3000` in dev. The production topology must be same-origin through the proxy.
- **Placeholder text.** "Search sources, rules, offenses, anomalies" truncates with an ellipsis at 1024.
- **CSS dimension literals.** A few non-token dimensions appear in `globals.css`: `64px` (the Ctrl K hint padding), `520px` (the search max width) and `44ch` (the status list width). They are not colour, spacing-scale or type literals, but they are not in `tokens.css`.
- **Refresh scope.** `router.refresh()` re-runs server components only. Client-fetched page data is not re-read, because no page has a client fetch registered yet.
- **Search request count.** `GET /anomalies` with a 403 is not cached as forbidden, so a role without anomaly access repeats that request per search. It is one cheap request.
- **Anomaly search matching.** It matches `log_source_name` only, per the spec. WAF-endpoint anomalies (which have no source name filter) match on their source name too.
- **Last-refreshed time.** It starts at mount, meaning "page loaded", and moves on each auto-refresh.
- **Tests not edited.** No existing test was edited, skipped or weakened. `AppNav.test.tsx` and `shellFixes.test.ts` pass unchanged.

## Review fixes (round 2)

### 1. Files changed
- `frontend/src/app/tokens.css`: four layout tokens added after `--header-h`: `--topsearch-max-w` (520px), `--topsearch-hint-room` (64px), `--topsearch-hint-pad-y` (1px), `--status-popover-w` (44ch).
- `frontend/src/app/globals.css`: the UI-1.5 rules reference those tokens; the narrow-sidebar block (max-width 1200px) clips `.brand-sub`.
- `docs/tasks/UI-1.5.report.md`: this section.
No test, `AppNav`, `shellFixes` or `layout.tsx` change.

### 2. What I did
- Literals moved to tokens (rendered values unchanged): `64px` -> `--topsearch-hint-room`; `520px` -> `--topsearch-max-w`; `44ch` -> `--status-popover-w`; the hint's `1px` vertical padding -> `--topsearch-hint-pad-y`. The scan of the `globals.css` additions and the five new shell components (no inline styles, no colour, size or type literals in the TSX) found nothing else. Left as they are: `1px` borders (structural, used throughout the file); `letter-spacing: 0.06em` on `.topsearch-group` (the same literal is already used by an existing rule, so it is the file's convention); `margin-top: 2px` on `.brand-sub` (pre-existing).
- Brand at 1024: the sidebar column is 196px wide at 1200px and below, which leaves 156px of text width. The subtitle needs about 190px, so an ellipsis would cut it to roughly "QRadar Anomaly & Inve...", which reads worse than leaving it out. I clipped only the subtitle in that block, with the `.sr-only` technique, so the link still carries the full wording for assistive tech. The brand is then one line, vertically centred in the 56px row. At 1280 and 1440 the column is 232px and the subtitle stays on one line. I used the 1200px block, not 1100px, because that is where the column narrows and the wrap begins. Type is unchanged.

### 3. Gates (from `frontend/`)
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, no output (clean)
- `npm test`: `Test Files  103 passed (103)` / `Tests  1666 passed (1666)`
- `npm run build`: succeeded (final lines `○  (Static)   prerendered as static content`, `ƒ  (Dynamic)  server-rendered on demand`); rebuilt once more with the fixture API base for the captures.
- Screenshots (fixture in `scratchpad/ui15b/`, outside the repo): all 12 captures have `scrollWidth == clientWidth` (no page overflow), header height 56px, no failed requests. I viewed 1024 behavior, 1280 fleet search, 1440 root unreachable and 1024 admin_search-cadence bell-unavailable: the brand fits its row; the search field and Ctrl K hint look as before. Paths (`D` = `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui15b/png`): `D/status-ok/behavior-{1440x900,1280x800,1024x768}.png`, `D/search-results/fleet-{same three}.png`, `D/status-unreachable/root-{same three}.png`, `D/bell-unavailable/admin_search-cadence-{same three}.png`.

### 4. Open points
- At 1200px and below the subtitle is not visible (it stays in the accessibility tree). Showing it there would need shorter wording, which this task excluded.
- No test pins the CSS clip; the brand markup test is unchanged.
