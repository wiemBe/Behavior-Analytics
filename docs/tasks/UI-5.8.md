# Task UI-5.8 — `/config-changes` and every loading and error boundary

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative: §4, §6, §8, §9, §10), `frontend/src/app/tokens.css`,
`frontend/src/components/ui/controls.css`, `docs/UI-REDESIGN-PLAN.md` **UR.7 Phase 5** (the UI-5.8
row), **UR.8.1**, and the `/config-changes` entry at the end of **UR.1.3**. Read
`docs/tasks/T-097.report.md` (T-097 wrote tests for 11 boundaries) and the review rulings in
`docs/tasks/UI-5.5.md` ("Rulings from UI-5.6's review that apply here").

## Part 1: `/config-changes`

Decision D-11 (`docs/PLANNER.md` §4): the route is **kept and restyled, not removed**, and stays
out of the navigation. Its copy says "Planned for Phase 3." and "Its data model and API exist (or
arrive) in Phase 3". That is no longer true: the README roadmap marks Phase 3 done, and no
collector or API fills configuration changes (check `backend/app/models/` for the
`config_change` model and confirm no route serves it; say what you found in the report).

- Use `PageHeader` (an `h1`) "Configuration changes".
- Replace the copy with one honest notice that says what is not there and why, in the product's
  voice, with no phase numbers and no promise: configuration changes are not collected; a data
  model exists, but no collector or API fills it, so this page has nothing to show. No link, no
  button, no table (the existing test pins their absence; keep that).
- **The orchestrator authorises editing `frontend/src/app/config-changes/page.test.tsx`** where it
  pins the old heading level and the old sentences: change those assertions to the new heading
  and copy, nothing else. List each edit in the report.

## Part 2: every `loading.tsx` and `error.tsx`

There are 32 boundary files today (`find frontend/src/app -name loading.tsx -o -name error.tsx`).

1. **Review each one** at 1440×900, 1280×800 and 1024×768 (force the state through the fixture:
   a slow response for loading, a failing one for error). A defect list in the report: route,
   what is wrong, the rule, the fix. Check at least:
   - a loading state mirrors its page's layout (header, then the first block's shape) so the
     page does not jump when data arrives: measure the first block's top before and after and
     report both numbers;
   - an error says what failed and what the analyst can do; never "Something went wrong", never
     an exclamation mark; it has `role="alert"` and a working retry (`reset`) where the
     boundary gets one;
   - tokens only, no inline style, no new literal; the same look across all boundaries (one
     skeleton style, one error style).
2. **Add the boundaries that are missing** for the data routes that fetch on the server and have
   none: `log-sources`, `log-sources/[id]`, `searches`, `searches/[id]`, `alerts`, `alerts/[id]`,
   `offenses`, `offenses/[id]`, `rules`, `rules/[id]`, `coverage` (check each: if a route renders
   on the client and handles its own loading, say so and skip it). Each new file gets a test for
   its state, in the style of T-097's tests.
3. Fix the existing ones within the allowed files. **The existing boundary tests stay green
   unedited**; if a fix needs an edit to one, it is the wrong fix: stop and report it.

## Tests

New tests for every new boundary and for markup you add. **Break-it** each new test: confirm it
fails, then restore.

## Screenshots and fixture

- Fixture API in `/home/efe/Documents/QRadar-Dash-work/ui58/fixture/` with the backend's real
  payload shapes and a control route to make a read slow or fail. The UI-5.6 fixture
  (`/home/efe/Documents/QRadar-Dash-work/ui56/fixture/`) shows the pattern; copy it.
- Ports 3158/8158/9158. Headless Chrome is `/usr/bin/google-chrome`.
- PNGs under `/home/efe/Documents/QRadar-Dash-work/ui58/png/{before,after}/`. Open them and say
  what you looked at. Nothing under `/tmp`.

## Allowed files

- `frontend/src/app/config-changes/page.tsx`, its `page.test.tsx` (only the authorised edits),
  a colocated stylesheet
- every `frontend/src/app/**/loading.tsx` and `error.tsx` (existing and new) and their tests
  (new tests only for existing files)
- one new shared stylesheet or component for the boundaries if you need one, under
  `frontend/src/components/boundary/` (new directory), with tests
- `frontend/src/app/tokens.css` (new tokens only, each justified in the report)
- `docs/tasks/UI-5.8.report.md` (new)

Not `globals.css`, not `components/ui/*`, not any `page.tsx` other than `/config-changes`.

## Gates

From `frontend/` with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim into the report
(AGENTS.md §6 format). Do not commit. Stop every process you start, by PID. Never `pkill -f` or
`killall`.
