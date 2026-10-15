# UI-4.2 report

## 1. Files changed
- `frontend/src/widgets/useDashboard.ts` (new, Codex, fixed): reducer plus hook; saved/template source, saved and current layout, structural `dirty`, save state, `beforeunload` only while dirty, save / Reload / Save as a copy. A refused `PUT /default` after the create is no longer reported as a failed save (added by me).
- `frontend/src/widgets/useDashboard.test.ts` (new): reducer transitions, structural dirty, revision kept, conflict keeps layout, Reload, copy, template create plus default, refused default, beforeunload.
- `frontend/src/widgets/DashboardSaveDialogs.tsx` (new, Codex): native `<dialog>`s for the name-taken case, the conflict, the discard guard and layout confirms.
- `frontend/src/widgets/DashboardView.tsx`: controlled by `useDashboard`; Done saves when dirty; Save and Discard outside Customize (also below 12 columns, which Codex had missed); one polite status line; interim "not saved" copy removed.
- `frontend/src/widgets/DashboardView.test.tsx`: new tests (clean Done, failed Done, Save/Discard, narrow viewport) and one replaced test (below).
- `frontend/src/widgets/DashboardMenu.tsx` + test: Restore defaults (known template key), Reset to empty (no key), neither plus a reason (unknown key); navigating actions go through the guard.
- `frontend/src/widgets/templates.ts`: `QRadar Administration`, `Behavioral Analytics`, `Log Source Monitoring`, `WAF / IPS Monitoring` (versioned keys, live widgets only), `templateByKey`, template descriptions.
- `frontend/src/widgets/registry.test.ts`: one `deriveLayout` no-overlap test per new template at 6 and 1 columns (additions only).
- `frontend/src/widgets/dashboard.css`: `.dg-save-status`, a quiet one-line variant (no box).
- `frontend/src/components/home/HomeDashboard.tsx` + test: `DashboardSession` wires the reducer, all dialogs, the permission rules, the guard, and a capture-phase click guard for sidebar links.
- `docs/tasks/UI-4.2.report.md` (this file). Not touched: shell files, `dashboardApi.ts`, `tokens.css`, backend.

## Inherited from Codex
Kept: the whole reducer/hook design, dialogs component, menu restore/reset, the four templates' skeleton, the controlled `DashboardView`, the `DashboardSession` split and the capture-phase sidebar guard, most tests.
Changed (found by running the inherited work):
- Status line hid the save error (showed only "Unsaved changes"); now shows both. 3 inherited tests were red for this and for ambiguous queries (a 403 text shown twice, two "Discard" buttons); fixed the code or scoped the queries.
- Save/Discard were missing below 12 columns (a per-widget range change is still possible there). Added, with a test.
- The status line is no longer a boxed card (it made a 60 px jump on the first edit) and no longer repeats the header's "cannot save" reason (the `saveReason` prop is gone; the header line already says it).
- Behavioral Analytics and WAF / IPS templates contained widgets that render "Choose a log source / endpoint" empty states by default (`chart.eps_observed_expected`, `waf.endpoint_traffic`). Removed and named in the template descriptions. Layouts of Administration and Behavioral retiled to remove half-empty cards; row heights tuned from screenshots.
- A refused `PUT /default` after a successful create no longer reads as a failed save.
- Added tests: 422 and 409-limit on a saved dashboard, conflict Reload re-read, shared-not-owned and `/me`-failed send no write, menu navigation guard, sidebar-link guard, narrow-viewport Save/Discard.
Reverted: nothing.
Existing-test edits (all mandated by "remove UI-4.1's interim line"/the new header): `DashboardView.test.tsx` "says plainly that changes last for this session only" became "removes the interim unsaved-session copy ..." (asserts the old text is gone); `HomeDashboard.test.tsx` "says plainly that changes are not saved while customizing..." became the "keeps dashboard navigation available while customizing and removes the interim no-save copy" test (the switcher and menu now stay in Customize, needed for Restore defaults); the `fetch` stub and the DashboardView mock gained write responses and a `leading` function; `DashboardMenu.test.tsx` `setup` gained an `extra` argument. No test was skipped or deleted outside these.

## 2. What was done
Spec items 1 to 5 are implemented as written. Decisions: Customize now shows the switcher and menu (Restore defaults lives there; switching is guarded by the dirty check). The unsaved-changes guard also covers sidebar links through a capture-phase `click` listener scoped to the page, active only while dirty (no shell edit). Sidebar links opened with modifier keys or `target=_blank` are not guarded; browser Back/Forward is covered only by `beforeunload` when leaving the app, not for in-app history navigation (open gap).
Header stays 72 px at 1440 and 1280 in clean, unsaved, save-failed, customize and read-only states, and 72 px at 1024 (unsaved Save/Discard fits beside the title once the "Widen the window" hint is hidden while dirty).

## 3. Gates (from `frontend/`)
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: clean (nothing after `> tsc --noEmit`)
- `npx vitest run --no-cache`: `Test Files  194 passed (194)` / `Tests  2643 passed | 2 todo (2645)`
- `npm run build`: `✓ Compiled successfully in 6.5s`
- Break-its (each failed the named tests, then restored): sending `PUT` without `expected_revision` (2 fail); conflict drops the local layout (3 fail); Save offered without `dashboard:write` (`mayWrite` forced true: 4 fail); guard skipped when dirty (3 fail); `beforeunload` always registered (1 fail); Save/Discard removed from the narrow branch (1 fail); a template tile overlapping (existing geometry test fails).
- `verify.mjs` (UI-4.1's copy, ports 3142/8142/9142, `/home/efe/Documents/QRadar-Dash-work/ui42/verify.mjs`): exit 0; `geometryDeltaPx` open 0.75 / close 0; `hydrationWarnings: []`; UI-4.1's density comparison (`logs/ui33-output.json`) identical to the previous run apart from request order. The harness needed two edits because the page now registers `beforeunload` while dirty: it accepts the browser dialog and reseeds the fixture on each navigate; its Customize selector was also changed (the switcher is now the first secondary button). Output: `logs/verify-output.txt`.
- `shots.mjs` (exit 0): header 72/72/72 px at all states per viewport; page overflow 0 at all three.

## 4. Open points
- Customize is unavailable at 1024x768 ("Widen the window to customize"), so Restore defaults has no 1024 PNG.
- In-app history navigation (Back) while dirty is not guarded; only `beforeunload`, link clicks and the switcher/menu are.
- A saved dashboard that now offers Reset to empty when it has no `template_key` also applies to dashboards created from a template by an older client without a key; that is per spec.
- The read-only caller can still enter Customize (session-only changes), as in UI-4.1.
- `afterReload` shows no "Saved"/line after Reload by design.

## 5. PNGs (`/home/efe/Documents/QRadar-Dash-work/ui42/png/`, `<name>-<WxH>.png`, each at 1440x900, 1280x800, 1024x768 except restore-defaults)
unsaved, discard-guard, save-failed, conflict, restore-defaults (1440, 1280), read-only, template-admin, template-behavior, template-logsource, template-waf. Opened and checked: unsaved 1440/1024, save-failed 1440, conflict 1440/1024, restore-defaults 1440, read-only 1280/1024, discard-guard 1280/1024, template-admin 1440, template-behavior 1440, template-logsource 1280, template-waf 1440/1280/1024 (and earlier versions of admin/behavior/waf that led to the template changes above). Seen: header one row, "Unsaved changes." as one quiet line, no clipping, dialogs centred with focus ring on the field, no empty-state widgets in the templates, no page overflow. Fixture: `ui42/fixture/server.py` (PUT with revision check; `?bump=<index>`, `?putfail=1`, `?keep=1`). Processes started were stopped by PID; ports 3142/8142/9142 are closed. Nothing committed.
