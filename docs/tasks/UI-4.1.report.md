# UI-4.1 report

## 1. Files changed
- `frontend/src/lib/api.ts`: `request` exported; a `204` resolves to `undefined` (no existing route answers 204).
- `frontend/src/lib/dashboardApi.ts` (new): typed client (list, get, create, patch, delete, duplicate, setDefault, replace with `saved`/`conflict` result, `fetchMe`), `dashboardReads`, `readLayout`, `describeRefusal`.
- `frontend/src/widgets/DashboardSwitcher.tsx`, `DashboardMenu.tsx` (new): disclosure switcher; menu with native `<dialog>` forms, `dashboardAccess` (pure, fail closed).
- `frontend/src/widgets/DashboardView.tsx`: `saved` prop (plain "Changes are not saved" line) and `leading` prop (switcher and menu, left out while customizing).
- `frontend/src/widgets/dashboard.css`, `frontend/src/app/tokens.css`: new rules; new tokens `--dash-z-pop`, `--dash-pop-w`, `--dash-menu-w`, `--dash-modal-w`.
- `frontend/src/components/home/HomeDashboard.tsx`: `/` loads `?dashboard=` / default / template; all states; skeleton.
- `frontend/src/app/page.tsx`: Suspense boundary (the page now reads `useSearchParams`).
- Tests (new): `dashboardApi.test.ts`, `DashboardSwitcher.test.tsx`, `DashboardMenu.test.tsx`, `HomeDashboard.test.tsx`.

### Existing-test edits (`frontend/src/app/page.test.tsx`)
- `next/navigation` mock gained `useSearchParams`; `useApiResource` mock returns `idle` for a null key; `stateFor` answers `/dashboards*` (none saved) and `/me`. Needed because the page now reads them.
- The h1 assertions changed from "Dashboard" to "SOC Overview" (two places plus the heading-list first entry). This is mandated by the spec (the template's title is "SOC Overview"). All other assertions are unchanged.

## 2. What was done
- `/`: `?dashboard=<uuid>` wins, else the `is_default` dashboard, else the SOC Overview template (unsaved). `?dashboard=template` is a reserved value for the template when a default is set (the switcher uses it; `/` alone when no default).
- States: loading (header plus template-geometry skeleton, measured 0 px jump); list failed (template plus one line with the reason and "not saved"); 404 (message and link to `/`); other detail failure; unknown layout `v` (names the version, offers the template); no dashboards (template, nothing error-like); unknown widget type renders the existing "Unsupported widget" frame.
- Template title "SOC Overview" with "Template, not saved" and the old description in the quiet meta row (so the header stays 72 px). A saved dashboard's own description is a line only when it has one.
- Switcher, menu, permissions (`dashboard:write`, `admin:dashboards`, owner, `/me` failure, a later 403), list re-read after each write (key bump; navigating writes drop the held list), server `detail` shown in dialogs.
- Decision: while customizing, switcher and menu are hidden (the session line needs the room; switching would drop in-memory edits). UI-4.2 can revisit.
- Menu entries for shared dashboards not owned: only "Duplicate into my dashboards" (admins included, per spec).

## 3. Gates (from `frontend/`)
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: clean (no output after `tsc --noEmit`)
- `npx vitest run --no-cache`: `Test Files  192 passed (192)` / `Tests  2575 passed | 8 todo (2583)`
- `npm run build`: `✓ Compiled successfully in 5.8s`
- Break-its (each failed the expected tests, then restored): write controls without `dashboard:write` (3 fail), `/me` failure grants write (1), list failure blank page (2), default ignored (8).
- `verify.mjs` (copy of ui35b, ports 3140/8140/9140): exit 0, no thrown check; overflow assertions pass; UI-3.3 density comparison (`ui33-output.json`) is identical to the ui35b run (same widgets "grew" as before, no new overflow); hydration warnings `[]`. Output: `/home/efe/Documents/QRadar-Dash-work/ui41/logs/verify-output.txt`, `ui33-output.json`, `ui35-output.json`.
- Header row (`shots-output.json`): at 1440x900 and 1280x800 the header is 72 px in every state (default, customizing, read-only, `/me` failed, list failed, template, 404, shared) with the actions beside the title (one row). Actions group at 1440: left 1096 to right 1393 (Dashboards, Manage, Customize), 1280: 936 to 1233. At 1024x768 the actions stay beside the title; the title block wraps its qualifiers (112 px in read-only/shared, 107 template, 72 default). Page overflow 0 at all three. Loading vs loaded grid top: 184/184 (1440, 1280), 176/176 (1024).
- Browser console: no errors except the deliberate 503/500/404 responses.

## 4. Open points
- A saved dashboard that has a description adds a 21 px line (loading placeholder does not reserve it). No UI sets descriptions yet.
- `--danger` (used by existing `.dg-arrange-remove`) is not defined in tokens.css; not touched.
- The session line stays absolutely positioned (existing hack); a very long dashboard name could collide with it.
- Fixture: `/home/efe/Documents/QRadar-Dash-work/ui41/fixture/server.py` (control route `/__control/dash?write=&share=&me=&list=&seed=&delay=`).

## 5. PNGs (`/home/efe/Documents/QRadar-Dash-work/ui41/png/`, each at 1440x900, 1280x800, 1024x768)
switcher-open, menu-open, rename-409, delete-confirm, read-only, me-failed, list-failed, list-failed-switcher, template-no-dashboards, template-menu, not-found, shared-not-owned, loading; customize-saved and template-customize (1440 and 1280 only). File name pattern `<name>-<WxH>.png`. verify.mjs PNGs in `png/verify/`.

## Round 2

### Inherited work reviewed
- The interrupted round-2 work had already introduced `asSentence` / `describeRefusal`, routed
  dashboard-menu write failures through it, added an initial dialog-field rule, and added partial
  tests for all three review points.
- The field rule still depended on global browser/control styling and did not guarantee matching
  input/select heights. The template separator was still generated after the first item, so it
  could remain on the previous line when the description wrapped. The refusal tests did not yet
  pin every requested input shape exactly.

### Changes and acceptance evidence
- `frontend/src/widgets/dashboard.css` and `frontend/src/app/tokens.css`: the dialog input and
  select now carry the complete token-only control contract locally (38 px height, padding, text
  and background colours, border, radius, inherited family, size and line height) plus a visible
  tokenised `:focus-visible` outline. This selector covers the name input used by new, rename and
  duplicate, and the template select used by new.
- `frontend/src/components/home/HomeDashboard.tsx`: the template qualifier and description are
  separate `.dg-qualifier` items with no punctuation separator. This removes the possibility of a
  separator being stranded when the description starts a new line. The same header harness checked
  default, read-only, shared and template states at every viewport: header heights were 72/72/72/72
  px at 1440 and 1280; 72/112/112/107 px at 1024; actions remained beside the title in every case.
- `frontend/src/lib/dashboardApi.ts` and `frontend/src/widgets/DashboardMenu.tsx`: every dashboard
  write refusal displayed in a form dialog, a direct-action dialog or the page-level 403 line goes
  through `describeRefusal`. It capitalises the first character, adds one terminal full stop, strips
  Pydantic's `Value error, ` prefix, and does not expose an arbitrary non-`ApiError` message.
- Tests now pin the whole field contract and focus ring; the structural two-item, separator-free
  template metadata; lowercase and already-punctuated details; Pydantic list detail; and a
  non-`ApiError`. The final focused run was `Test Files  3 passed (3)` / `Tests  73 passed (73)`.
- Final capture measurements report input/select heights of 38/38 px, the rename refusal exactly as
  `You already have a dashboard named 'Night shift'.`, and page overflow 0 at all three viewports.

### Break-its
- Removed the dialog field radius: `DashboardMenu.test.tsx` failed the field-contract test
  (`1 failed, 25 passed`); restored it and the suite passed.
- Put the separator back into `Template, not saved`: the focused home test failed
  (`1 failed, 23 skipped`); restored the two separator-free items and the suite passed.
- Bypassed `asSentence`: the refusal-focused run failed the lowercase and punctuation assertions
  (`3 failed, 3 passed, 17 skipped`); restored it and the suite passed.

### Gates and harness
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: clean; no diagnostics after `> tsc --noEmit`.
- `npx vitest run --no-cache`: `Test Files  192 passed (192)` /
  `Tests  2579 passed | 8 todo (2587)`.
- `npm run build`: `✓ Compiled successfully in 12.5s` (exit 0; 25/25 static pages generated).
- Two earlier full Vitest attempts under host load timed out: first `Test Files  1 failed | 191 passed
  (192)` / `Tests  1 failed | 2578 passed | 8 todo (2587)` in the unrelated rules-statistics test;
  second `Test Files  2 failed | 190 passed (192)` / `Tests  2 failed | 2577 passed | 8 todo
  (2587)` in that test and one home test. The rules-statistics test immediately passed alone, the
  home focused suite passed, and the unchanged exact full command then produced the green result
  above.
- `rebuild.sh` completed its production build, but its restart reported `EADDRINUSE` because the
  inherited 3140 server was outside this session's PID namespace. For a fresh capture, the harness
  was temporarily pointed at isolated ports 3141/8141, built and run there, and then restored
  exactly to 3140/8140/9140. `shots.mjs` exited 0. The tracked Next PID 707306 and fixture PID
  693599 were stopped through their owning sessions; ports 3141 and 8141 were closed afterward.
- A non-required `verify.mjs` attempt did not complete: after correcting its fixture seed, the old
  verifier stopped at line 218 because it expects `button.action.secondary`; UI-4.1's dashboard
  control is not that selector. Its requested checks were instead exercised by the updated
  `shots.mjs` run and the PNG review. `git diff --check` is clean.

### PNGs opened and inspected
- `/home/efe/Documents/QRadar-Dash-work/ui41/png/template-no-dashboards-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/ui41/png/template-no-dashboards-1280x800.png`
- `/home/efe/Documents/QRadar-Dash-work/ui41/png/template-no-dashboards-1024x768.png`
- `/home/efe/Documents/QRadar-Dash-work/ui41/png/rename-409-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/ui41/png/rename-409-1280x800.png`
- `/home/efe/Documents/QRadar-Dash-work/ui41/png/rename-409-1024x768.png`
- `/home/efe/Documents/QRadar-Dash-work/ui41/png/new-dashboard-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/ui41/png/new-dashboard-1280x800.png`
- `/home/efe/Documents/QRadar-Dash-work/ui41/png/new-dashboard-1024x768.png`

All nine captures were visually checked. The template has no dangling separator, both dialog
controls have the same height and treatment, the focused name input has a visible ring, the refusal
is a complete sentence, and there is no clipping or page overflow at any requested viewport.
