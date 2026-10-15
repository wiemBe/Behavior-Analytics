# Task UI-4.2 — saving a dashboard, the conflict dialog, restore defaults, the templates

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative), `frontend/src/app/tokens.css`, and in
`docs/UI-REDESIGN-PLAN.md` **UR.5.3**, **UR.5.5** (the two-writer case, limits, RBAC) and
**UR.5.7** (the templates). Read `docs/tasks/UI-4.1.md` and its report: this task builds on the
client (`lib/dashboardApi.ts`, including `replaceDashboard`'s conflict result), the switcher, the
menu and the `/` loading that UI-4.1 merged.

## Build

### 1. One source of truth for the dashboard being edited: `widgets/useDashboard.ts` (new)

A hook (or reducer) holding: the saved dashboard (`id`, `name`, `revision`, `template_key`,
`template_version`, owner, visibility) or "the template, unsaved"; the saved layout; the current
layout; `dirty` (current differs from saved, compared structurally, not by reference); and the
save state (`idle | saving | saved | conflict | error`). Pure transitions, unit-tested without a
DOM. `DashboardView` keeps its in-memory editing; it reports layout changes to this hook instead
of owning the only copy.

### 2. Save

- **"Done"** in Customize saves a dirty, writable dashboard: `PUT` with `name`, `description`,
  `layout` and `expected_revision`. On success the new revision is kept and a polite live-region
  notice says "Saved". Not dirty: "Done" just leaves Customize.
- **Outside Customize**, a per-widget range or a configure change also makes the dashboard dirty.
  The header then shows "Unsaved changes" with **Save** and **Discard**. One rule for both paths:
  the server copy changes only on an explicit Save or Done.
- **The template, unsaved**, with `dashboard:write`: the first Save or Done creates the
  dashboard (`POST`, name "SOC Overview" or the template's name, `template_key`,
  `template_version`), and, when the caller has no default yet, sets it as default (`PUT
  /default`), so `/` loads it next time. If the name is taken (`409` with a string `detail`), a
  dialog asks for another name, showing the server's text.
- **Without `dashboard:write`**, on a shared dashboard the caller does not own, or after `/me`
  failed: no Save, Discard stays (session-only changes), and the header line says why changes
  cannot be saved. Never send a write that the permissions already rule out.
- **Failures:** the layout is never lost.
  - `409` conflict (`{detail: {message, current_revision}}`): a native `<dialog>`, "This dashboard
    changed since you opened it", with **Reload** (discard local changes, re-read the dashboard)
    and **Save as a copy** (`POST` the current layout under "Copy of <name>", editable, then switch
    to it). Nothing is overwritten silently; there is no "overwrite" button.
  - `409` limit or name, `422`: the server's `detail` in the header line; stay in the current mode
    with changes kept.
  - `403`: as UI-4.1 (hide write controls, say why), changes kept in memory.
  - A network failure: say that saving failed and that the changes are still here; Save stays
    available.
- Remove UI-4.1's interim "changes are not saved" line where saving now exists.

### 3. Restore defaults

In Customize, the menu offers **Restore defaults** for a dashboard with a `template_key`: a confirm
naming the template and its current version, then the layout becomes that template's current
layout (dirty; saved on Done like any edit). A dashboard without a template key offers **Reset to
empty** instead (confirm; an empty layout). An unknown `template_key` (an older or newer client's)
offers neither and says why.

### 4. The other four templates (`widgets/templates.ts`)

Add, per UR.5.7, `QRadar Administration`, `Behavioral Analytics`, `Log Source Monitoring` and
`WAF / IPS Monitoring`, with versioned keys. **Only widgets whose registry `status` is `live`**
(the registry test enforces it); a widget left out is named in the template's description (for
example "IPS anomalies are left out until …" if `ips.source_anomalies` is not live when you
start). Lay each out on the 12-column grid with no overlap and no gap a reader would read as
missing data; check `deriveLayout` at 6 and 1 columns. The "New dashboard" dialog of UI-4.1 lists
all five.

### 5. The unsaved-changes guard

With unsaved changes:
- switching dashboards in the switcher, and menu actions that navigate (new, duplicate, delete
  of another), ask first: "Discard unsaved changes to <name>?" with **Discard** and **Keep
  editing** (a native `<dialog>`; focus returns to the trigger);
- leaving the page or reloading triggers the browser's own `beforeunload` prompt;
- the sidebar's in-app links: measure whether a guard is possible without editing shell files
  (for example a capture-phase click handler scoped to the dashboard page). If it is not possible
  within the allowed files, leave it and state the gap in the report. Do not edit the shell.

## Tests (vitest, beside each file)

- `useDashboard`: each transition; `dirty` is structural; save success keeps the new revision;
  conflict keeps the local layout; Reload discards it; Save as a copy posts the current layout.
- Save paths: Done when dirty and when clean; Save/Discard outside Customize; the template's
  first save creates and, with no default, sets default; the name-taken dialog.
- Every failure branch above, asserting the layout is still there afterwards.
- No write is sent without `dashboard:write`, on a shared dashboard not owned, or after `/me`
  failed (assert the client was not called).
- Restore defaults and Reset to empty: confirm, then the layout; unknown template key.
- Templates: the existing registry test covers live types and geometry; add one per new template
  that `deriveLayout` at 6 and 1 columns has no overlap.
- Guard: switcher and menu navigation ask when dirty and not when clean; `beforeunload` is
  registered only while dirty.
- **Break-its** (confirm a test fails, then restore): a conflict overwrites (sends without
  `expected_revision`); the conflict path drops the local layout; Save is offered without
  `dashboard:write`; the guard is skipped when dirty.
- Never weaken, skip or delete an existing test; list any existing-test edit with its reason.

## Screenshots

Copy UI-4.1's fixture (`/home/efe/Documents/QRadar-Dash-work/ui41/fixture/`) to
`/home/efe/Documents/QRadar-Dash-work/ui42/fixture/` (never edit the original). Add a control that
bumps a dashboard's revision behind the client's back (to show the conflict), and one that makes
the next `PUT` fail.
- Ports 3142/8142/9142. Headless Chrome is `/usr/bin/google-chrome`.
- At 1440×900, 1280×800 and 1024×768: the "Unsaved changes" header; the conflict dialog; the
  discard guard; the Restore defaults confirm; the save-failed line; each of the four new templates
  rendered (read mode, first screen); the read-only header line.
- PNGs to `/home/efe/Documents/QRadar-Dash-work/ui42/png/`. Nothing under `/tmp`. Look at every
  PNG. Run a copy of `/home/efe/Documents/QRadar-Dash-work/ui33/verify.mjs` and quote its output
  (no added overflow; no taller header row).

## Allowed files

- `frontend/src/widgets/useDashboard.ts` (new) + test
- `frontend/src/widgets/templates.ts`, `frontend/src/widgets/registry.test.ts` (new template
  tests only)
- `frontend/src/widgets/DashboardView.tsx`, `DashboardSwitcher.tsx`, `DashboardMenu.tsx`, new
  dialog components in `frontend/src/widgets/` + tests, `frontend/src/widgets/dashboard.css`
- `frontend/src/components/home/HomeDashboard.tsx` + test
- `frontend/src/lib/dashboardApi.ts` (only if UI-4.1 left a gap; say which)
- `frontend/src/app/tokens.css` (new tokens only)
- `docs/tasks/UI-4.2.report.md` (new)

No new dependency. No backend change: if the API lacks something, stop and report it.

## Gates

From `frontend/` with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim into the report
(AGENTS.md §6 format), with the PNG list and the `verify.mjs` output. Do not commit. Stop every
process you start, by PID. Never `pkill -f` or `killall`.
