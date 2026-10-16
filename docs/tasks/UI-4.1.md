# Task UI-4.1 — saved dashboards: the client, the switcher, and `/` loads the default

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative), `frontend/src/app/tokens.css`, and in
`docs/UI-REDESIGN-PLAN.md` the sections **UR.5.3** (interactions), **UR.5.5** (persistence: the
layout JSON, limits, ownership, RBAC, the API table), **UR.5.6** and **UR.5.7** (templates).
The backend is merged (B-4, report `docs/tasks/B-4.report.md`; routes
`backend/app/api/routes/dashboards.py`, schemas `backend/app/schemas/dashboard.py`, service
`backend/app/services/dashboards.py`; `GET /api/v1/me` in `backend/app/api/routes/me.py`).

**Ruling (orchestrator):** the plan's UI-4.3 ("`/` loads the user's default") is folded into this
task, because the switcher cannot be seen without it, as UI-3.1 and UI-3.4 were folded. UI-4.2
stays separate: saving a layout on "Done" (`PUT` with `expected_revision` and the `409` dialog),
"Restore defaults", the other four templates and the unsaved-changes guard.

## Build

### 1. The client: `frontend/src/lib/dashboardApi.ts` (new)

Typed functions over the existing `request` / `ApiError` pattern of `lib/api.ts` (reuse it; do
not write a second fetch wrapper). Types mirror the backend schemas exactly (`DashboardListItem`,
`DashboardOut`, `PagedDashboards`, `DefaultDashboardOut`, the layout of `widgets/types.ts`).
- `listDashboards()`, `getDashboard(id)`, `createDashboard({name, layout, template_key,
  template_version})`, `patchDashboard(id, {name?, description?, visibility?})`,
  `deleteDashboard(id)`, `duplicateDashboard(id)`, `setDefaultDashboard(id | null)`.
- `replaceDashboard(id, {name, description, layout, expected_revision})` for UI-4.2. It returns a
  discriminated result: `{kind: "saved", dashboard}` or `{kind: "conflict", message,
  current_revision}` read from the `409` body, which FastAPI wraps as
  `{"detail": {"message": …, "current_revision": …}}`. A `409` whose `detail` is a string (the
  duplicate-name refusal) is not a conflict: it throws `ApiError`, as does any other error.
  `lib/api.ts` `request` already keeps a non-string `detail` on `ApiError.detail`; use that.
- `request` is module-private and always calls `res.json()`, which fails on `DELETE`'s `204`.
  Export it (or a 204-safe variant beside it) from `lib/api.ts` without changing its behaviour for
  existing callers. Unit-test both branches; no UI uses it yet.
- `fetchMe()` → `{subject, email, permissions, effective_permissions}` from `GET /me`.
- Errors keep the server's `detail` text (for example "you already have a dashboard named
  'X'", "the limit is 20 dashboards per owner"); the UI shows that text, not a generic one.

### 2. `/` loads the user's default

`components/home/HomeDashboard.tsx` (and `app/page.tsx` only if needed) stops hard-wiring
`SOC_OVERVIEW.layout`:
- Read `GET /dashboards` and `GET /me` (both through `useApiResource` or the same client pattern
  the page already uses, so the shell's refresh does not re-create the page).
- `/?dashboard=<uuid>`: that dashboard. Otherwise the one with `is_default`. Otherwise the **SOC
  Overview template, unsaved**.
- The dashboard's `layout` goes to `DashboardView`. A layout whose `v` the client does not know is
  not guessed at: the page shows an error state naming the version and offers the template.
  A widget `type` the registry does not know renders as the existing unsupported placeholder (check
  `widgets/runtime.tsx` / `Placeholder.tsx`; it must not throw).
- States, each designed (guidelines §4, §6):
  - loading: the header and the grid skeleton the page already has; no layout jump when the
    dashboard arrives (measure it);
  - the list read failed: the template is shown, with one line saying saved dashboards could not
    be loaded, the reason, and that changes here are not saved;
  - `?dashboard=` names a dashboard that does not exist or is not visible (`404`): say so, and
    link to the default;
  - no dashboards: the template, with nothing that reads as an error.
- The page title (`PageHeader`) shows the dashboard's name; the template shows "SOC Overview" and
  a quiet "Template, not saved" qualifier. Keep the existing description line only if it still
  describes what is shown (it is template-specific copy today).

### 3. The switcher and the menu: `widgets/DashboardSwitcher.tsx`, `widgets/DashboardMenu.tsx`

In the page header's actions, beside Customize.
- **Switcher:** a disclosure (button + list, keyboard operable, `aria-expanded`, `Esc` closes,
  focus returns to the button) listing the caller's dashboards and the shared ones: name, a
  "Shared" marker on shared ones, a "Default" marker on the default, and the template entry
  "SOC Overview (template)". Selecting one navigates to `/?dashboard=<id>` (the template: `/`
  with no parameter when no default is set). The list is the list read's; never invent entries.
- **Menu** (for the dashboard shown), with native `<dialog>` forms where input is needed:
  - **New dashboard from template**: name (1–80 characters, trimmed) + the template (only SOC
    Overview exists until UI-4.2); `POST` with `template_key` and `template_version`; then switch
    to it.
  - **Rename** (`PATCH` name).
  - **Duplicate** (`POST …/duplicate`); then switch to the copy. Offered on shared dashboards the
    caller does not own, as "Duplicate into my dashboards".
  - **Delete** with a confirm that names the dashboard; then switch to the default, or the
    template.
  - **Set as default** / **Clear default** (`PUT /default`).
  - **Share with everyone** / **Make private** (`PATCH visibility`): only for the owner, and only
    with `admin:dashboards`.
  - The template entry offers only "New dashboard from template" (saving the template under a
    name).
  - A refused write shows the server's `detail` in the dialog (duplicate name, the 20-dashboard
    limit, `422` validation). The dialog stays open so the analyst can correct it.
  - After each write the list is re-read (the server is the source of truth; no optimistic list).
- **Permissions** (from `/me`'s `effective_permissions`):
  - without `dashboard:write`: no create, rename, duplicate, delete, default or share control is
    rendered, and one quiet line says the role cannot save dashboards; the switcher still works;
  - a dashboard owned by someone else (shared): read-only; only "Duplicate into my dashboards";
  - `/me` failed: fail closed (as without `dashboard:write`), with the reason in that line;
  - a write that returns `403` anyway: hide the write controls and say why.
- **Customize in this task** stays as today (in memory). On a saved dashboard, while customizing,
  the header says plainly that changes are not saved. UI-4.2 replaces this with save on "Done";
  both land before `main`, so this interim copy never ships.

## Tests (vitest, beside each file)

- The client: each function's method, path and body; the `409` conflict branch and the other-error
  branch of `replaceDashboard`; the server `detail` is kept.
- `/`: default chosen; `?dashboard=` wins; no dashboards → template; list failure → template plus
  the line; `404` → the message and the link; unknown layout `v` → the error state; an unknown
  widget type renders the placeholder.
- Switcher: lists own, shared, default markers and the template entry; keyboard open/close;
  focus return; selection navigates.
- Menu: each action's request and the re-read; the confirm on delete; the server's `detail` shown
  in the dialog on `409`/`422`; the controls hidden without `dashboard:write`, on a shared
  dashboard not owned, when `/me` fails, and after a `403`; share only with `admin:dashboards`.
- **Break-its** (confirm a test fails, then restore): the write controls render without
  `dashboard:write`; `/me` failure grants write; the list failure shows a blank page instead of
  the template; the default is ignored.
- Never weaken, skip or delete an existing test. Existing Home tests that assume the hard-wired
  template may need the client mocked; that is allowed if their assertions stay the same — list
  each such edit in the report.

## Screenshots

Copy `/home/efe/Documents/QRadar-Dash-work/ui33/fixture/` to
`/home/efe/Documents/QRadar-Dash-work/ui41/fixture/` (never edit the original) and add in-memory,
stateful `/api/v1/dashboards` routes and `/api/v1/me`, with the backend's real payload shapes and
status codes (`201`, `204`, `409` bodies as the service sends them). A switch (environment
variable or a query on a fixture-only control route) selects: write permission or read-only;
`/me` failing; the list failing.
- Ports 3140/8140/9140. Headless Chrome is `/usr/bin/google-chrome`.
- At 1440×900, 1280×800 and 1024×768: `/` on the user's default with the switcher open; the menu
  open; the rename dialog with a `409` duplicate-name message; the delete confirm; the read-only
  state (no `dashboard:write`); the list-failed state; the template with no dashboards.
- PNGs to `/home/efe/Documents/QRadar-Dash-work/ui41/png/`. Nothing under `/tmp`. Look at every
  PNG. Run a copy of `/home/efe/Documents/QRadar-Dash-work/ui33/verify.mjs` and quote its output:
  no added overflow, and the header row does not grow a second line at 1440 and 1280.

## Allowed files

- `frontend/src/lib/dashboardApi.ts` (new) + test
- `frontend/src/lib/api.ts`: exporting `request` or adding a 204-safe variant only
- `frontend/src/widgets/DashboardSwitcher.tsx`, `frontend/src/widgets/DashboardMenu.tsx` (new)
  + tests
- `frontend/src/widgets/DashboardView.tsx` (header actions and the "not saved" line only),
  `frontend/src/widgets/dashboard.css`
- `frontend/src/components/home/HomeDashboard.tsx` + test, `frontend/src/app/page.tsx` + test
- `frontend/src/app/tokens.css` (new tokens only)
- `docs/tasks/UI-4.1.report.md` (new)

No new dependency. No backend change: if the API lacks something, stop and report it.

## Gates

From `frontend/` with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim into the report
(AGENTS.md §6 format), with the PNG list and the `verify.mjs` output. Do not commit. Stop every
process you start, by PID. Never `pkill -f` or `killall`.
