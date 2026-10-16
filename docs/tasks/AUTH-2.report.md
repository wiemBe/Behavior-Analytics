# AUTH-2 report

Status: complete. Sign-in, the session shell, user administration and directory settings are in the UI on top of the AUTH-1 API. One read-only backend route was added so the role picker lists whatever roles exist.

## Files changed

- `backend/app/schemas/users.py` — `RoleOut` (`name`, `permissions`) for the picker.
- `backend/app/api/routes/users.py` — `GET /api/v1/users/roles` on the existing `admin:users` router. One `select(Role.name, Role.permissions)` ordered by name.
- `backend/tests/unit/test_route_auth_guard.py` — the new route is on the OpenAPI guard (401 anonymous, 403 read-only).
- `backend/tests/integration/test_user_roles_api.py` — shape test: names `admin`, `analyst`, `viewer`; keys exactly `{name, permissions}`; no password fields.
- `frontend/src/lib/api.ts` — cookie credentials by origin, safe return paths, 401 redirect, optional session fields on `CallerInfo`.
- `frontend/src/lib/api.auth.test.ts` — credentials, return-path refusals, redirect exceptions, 422 detail kept as a list.
- `frontend/src/lib/nav.ts` — Administration gains Users (`admin:users`) and Directory (`admin:auth`).
- `frontend/src/lib/nav.test.ts` — expected Administration list includes those two items.
- `frontend/src/widgets/permissions.ts` — `admin:users` and `admin:auth` appended, matching `rbac.py`. This tree did not have them yet (T-115 adds the same pair).
- `frontend/src/app/layout.tsx` — `SessionProvider` and `AppFrame` wrap the app. Login is outside the shell.
- `frontend/src/components/shell/TopBar.tsx` — fifth cell is the signed-in account.
- `frontend/src/components/shell/Account.tsx` — "Signed in as {name}" and Sign out. The local-dev principal has the name and no Sign out.
- `frontend/src/components/shell/Account.test.tsx` — ready, local-dev, LOCAL, LDAP, still loading.
- `frontend/src/components/auth/auth.module.css` — token-only layout. Below 1100px the refreshed clock is clipped so the search field stays readable inside the fixed 56px header.
- `frontend/src/components/auth/access.ts` — permission check, visible nav, dev principal, signed-in name.
- `frontend/src/components/auth/access.test.ts` — exact grant, wildcards, empty permissions, dev principal.
- `frontend/src/components/auth/bindName.ts` — client port of the LDAP bind-name escape.
- `frontend/src/components/auth/bindName.test.ts` — the same cases as the backend escape tests.
- `frontend/src/components/auth/reasons.ts` — backend detail, including a 422 list, as the text to show.
- `frontend/src/components/auth/reasons.test.ts` — string, list, and a failed send.
- `frontend/src/components/auth/accountStatus.ts` — active, disabled, locked (a future `locked_until` only).
- `frontend/src/components/auth/accountStatus.test.ts` — locked only when the timestamp parses and is still ahead.
- `frontend/src/components/auth/client.ts` — login, logout, password, users, roles, directory. Cookie only. No storage.
- `frontend/src/components/auth/session.tsx` — `/me`, sign-out, stale-read guard.
- `frontend/src/components/auth/session.test.tsx` — loading without a provider, 401, errors, sign-out, a failed logout stays signed in.
- `frontend/src/components/auth/AppFrame.tsx` — bare `/login`, must-change gate, no-role gate, shell otherwise. Gated nav is hidden until the session is known.
- `frontend/src/components/auth/AppFrame.test.tsx` — those gates, and Users/Directory hidden for `read:offenses`.
- `frontend/src/components/auth/PasswordChange.tsx` — current, new, confirm. Backend reasons as sent.
- `frontend/src/components/auth/PasswordChange.test.tsx` — mismatch, 422 list, wrong current password, success, reload failure.
- `frontend/src/components/auth/NoRole.tsx` — the account exists and has no role. Sign out. No navigation.
- `frontend/src/components/auth/NoRole.test.tsx` — that copy.
- `frontend/src/components/auth/SignOutButton.tsx` — shared Sign out. A failed logout is shown and the session stays.
- `frontend/src/components/auth/ReasonList.tsx` — one banner, every reason.
- `frontend/src/app/login/page.tsx`, `LoginForm.tsx`, `loading.tsx`, `error.tsx` — sign-in outside the shell.
- `frontend/src/app/login/LoginForm.test.tsx` — idle, submitting, 401, 503, 429, must-change, reload failure, loading and error boundaries.
- `frontend/src/app/account/password/page.tsx`, `loading.tsx`, `error.tsx` — voluntary password change at `/account/password`. The required change is the gate, not a separate route.
- `frontend/src/app/admin/users/page.tsx`, `UsersPage.tsx`, `loading.tsx`, `error.tsx` — accounts table and actions.
- `frontend/src/app/admin/users/UsersPage.test.tsx` — empty, error, forbidden, each action and its error, including the last-admin 409 text.
- `frontend/src/app/admin/directory/page.tsx`, `DirectoryPage.tsx`, `loading.tsx`, `error.tsx` — directory form and connection test.
- `frontend/src/app/admin/directory/DirectoryPage.test.tsx` — no row, saved, 409 reload, 422 list, TLS failed, TLS ok with invalid credentials, success.
- `docs/tasks/AUTH-2.report.md` — this report.

`frontend/src/app/admin/page.tsx` was not edited. It is not in the allowed list. Users and Directory are in the sidebar.

## What this does

`/login` is outside the shell: username, password, one submit. A 401 shows the backend sentence as sent (`Invalid username or password`). A 503 says the directory could not be reached and that local accounts still work. A 429 says to wait. After login the app goes to a safe same-origin `next` path, or `/`. An unsafe path (`//`, a scheme, a control character, `/login` itself) becomes `/`.

Shared fetches in `api.ts` send the session cookie. `credentials` is `include` when the API origin differs from the page origin, and `same-origin` when it does not (a relative `/api/v1` stays same-origin). Server-side fetches stay `same-origin`. Nothing is written to `localStorage` or `sessionStorage`. A 401 redirects to `/login?next=<path>`, except `POST /auth/login` and a password change whose detail is `current password is wrong` (that 401 means the current password, and the session is still valid).

The top bar shows `Signed in as {username}` (`/me` has no display name) and Sign out (`POST /auth/logout`, then `/login`). The local-dev principal shows the name and no Sign out.

`must_change_password` replaces the shell with the password form (current, new, confirm) until it succeeds. A mismatch is checked in the browser and does not call the API. Backend 422 reasons are listed as sent. A user with no permissions sees one page: the account exists and has no role, and an administrator assigns roles under Administration → Users. No links.

Administration → Users (`admin:users`) lists username, source, display name, roles, status (active / disabled / locked, each named), and last login in UTC. Actions: create a local user (the initial password is shown once, then dismissed), pre-create an LDAP user, set roles from `GET /users/roles` plus any role already on the account, disable, enable, unlock, reset a local password. A 409 is the backend sentence, including the last-admin refusal. Each action says what it did.

Administration → Directory (`admin:auth`) edits `ldap_config` with its version. The bind preview uses the same escape as the backend. A stale version says someone else changed it and offers Reload. A 422 lists every reason. The test panel sends the draft plus a username and password, then clears both. It shows TLS, the certificate subject, issuer and expiry in UTC, bind, and latency, each as sent.

The role picker is the API list. The fixture used for screenshots includes `detection_engineer`; the UI does not hard-code roles.

## Screenshots

Headless Chrome against `http://127.0.0.1:3102`, fixture API `http://127.0.0.1:8102`. Fixture, capture script and PNGs are under `/home/efe/Documents/QRadar-Dash-work/auth2/`. Every shot has `documentElement.scrollWidth === clientWidth` (`overflow.json`). Shell pages measure 15px under the viewport because of the vertical scrollbar (1425/1425, 1265/1265, 1009/1009). No horizontal page overflow.

What the PNGs show:

- Login, idle: bare page, heading "Sign in", "A local account or a directory account.", username, password, one Sign in button. No shell and no logo.
- Login, 401: the same form, username `ada` still filled, and the banner "Invalid username or password".
- Must-change: "Change password", "This account cannot continue until the password is changed.", current / new / confirm, and Sign out. No navigation.
- No role: "The account norole exists and has no role." and "An administrator assigns roles under Administration → Users." Sign out. No links.
- Users: ada LOCAL Ada Lovelace, admin, active, `2026-10-15 12:00:00 UTC`; bea LDAP Bea Directory, analyst, active, last login "—"; cy LOCAL, disabled; dee LOCAL, locked. Signed in as ada, Sign out, status Connected. Users is the current nav item. Directory is in the same Administration group (on screen at 1440 and 1280; at 1024 the sidebar scroll starts at Users).
- Directory, saved: "Version 3, last saved 2026-10-15 12:00:00 UTC by ada.", sign-in enabled, `ldaps://dc1.example:636` then `ldaps://dc2.example:636`, StartTLS off, empty CA ("Empty uses the system trust store."), template `cn={username},ou=people,dc=example`, preview username `ada`.
- Directory, TLS failed: test fields cleared. TLS failed, reason "certificate verify failed", trust system, "No certificate was returned.", bind `not_attempted`, reason "TLS failed", latency `18 ms`.
- Directory, success: bind preview `cn=ada,ou=people,dc=example`. TLS ok, trust `ca_pem`, subject `CN=dc1.example`, issuer `CN=lab-ca`, expiry `2027-01-02 03:04:05 UTC`, bind ok, latency `24 ms`. Test fields cleared.

At 1440 the search placeholder is complete and the refreshed clock is visible. At 1024 the placeholder ends in an ellipsis ("Search sources, rules, a…") and the refreshed clock is visually clipped so the signed-in name and Sign out stay on the row. The clock remains in the accessibility tree.

Paths:

- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/login-idle-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/login-idle-1280x800.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/login-idle-1024x768.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/login-401-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/login-401-1280x800.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/login-401-1024x768.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/must-change-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/must-change-1280x800.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/must-change-1024x768.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/no-role-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/no-role-1280x800.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/no-role-1024x768.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/users-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/users-1280x800.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/users-1024x768.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/directory-saved-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/directory-saved-1280x800.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/directory-saved-1024x768.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/directory-tls-failed-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/directory-tls-failed-1280x800.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/directory-tls-failed-1024x768.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/directory-success-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/directory-success-1280x800.png`
- `/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/directory-success-1024x768.png`

## Break-it

One mutation per new test file, confirmed failing (exit 1), then the original file text restored. Leftover mutation strings are gone.

- `api.auth.test.ts` — an early return that accepted any path starting with `/` failed "refuses".
- `bindName.test.ts` — `*` escaped as `\2b` failed "escapes the same".
- `access.test.ts` — the exact-match clause returning false failed "grants an exact".
- `reasons.test.ts` — returning the status text instead of the list failed "lists every reason".
- `accountStatus.test.ts` — inverting the lock comparison failed "locks only".
- `session.test.tsx` — `router.replace("/stay")` failed "signs out and leaves".
- `Account.test.tsx` — always rendering Sign out failed "development principal".
- `PasswordChange.test.tsx` — skipping the confirmation check failed "confirmation differs".
- `NoRole.test.tsx` — "has a role" failed "says the account".
- `AppFrame.test.tsx` — ignoring `must_change_password` failed "password change is required".
- `LoginForm.test.tsx` — a "Wrong password" heading failed "does not guess".
- `UsersPage.test.tsx` — replacing the 409 text with "refused" failed "last-admin".
- `DirectoryPage.test.tsx` — a dash in place of the TLS reason failed "failed TLS".

## Gates

From `frontend/`, `~/.local/node/bin` on `PATH`. The full Vitest run is the tree before the topbar media query (CSS only). `colourLiterals.test.ts`, lint, typecheck and `npm run build` were run again after that CSS change.

```
> qradar-observability-frontend@0.1.0 lint
> next lint
✔ No ESLint warnings or errors
```

```
> qradar-observability-frontend@0.1.0 typecheck
> tsc --noEmit
```

Exit 0, no type errors.

```
Test Files  273 passed (273)
      Tests  3091 passed (3091)
   Duration  61.69s
```

`npx vitest run --no-cache src/app/colourLiterals.test.ts` after the CSS change:

```
Test Files  1 passed (1)
      Tests  31 passed (31)
```

```
> qradar-observability-frontend@0.1.0 build
> next build
✓ Compiled successfully in 6.6s
✓ Generating static pages (30/30)
```

Exit 0. Routes include `/login`, `/account/password`, `/admin/users`, `/admin/directory`. This build uses the default API base `http://localhost:8000/api/v1`. Screenshot builds that pointed `NEXT_PUBLIC_API_BASE_URL` at the fixture were overwritten by this one.

Backend, from `backend/` with `PYTHONPATH=.` and `ENCRYPTION_KEY` set, venv `/home/efe/Documents/QRadar-Dash/backend/.venv`:

```
All checks passed!
```

```
Success: no issues found in 237 source files
```

```
========= 3409 passed, 8 skipped, 1264 deselected in 139.46s (0:02:19) =========
```

(`pytest -o addopts='--strict-markers --tb=no' -m "not integration" -p no:warnings`. The project's `-q` addopts omit that footer; those runs exited 0.)

```
============================== 1 passed in 4.28s ===============================
```

(`tests/integration/test_user_roles_api.py` on `TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_auth2`.)

Processes started for screenshots were stopped by PID: fixture `802212`, `next start` `802213` and its `next-server` child `802273`. Chrome was started by the capture script and sent SIGTERM when the script finished (`790083`, `802433`, `804930`). A later check found no `google-chrome`, fixture, or `next-server` process, and nothing listening on 3102, 8102 or 9102.

## Open points

- `/me` has no display name, so the top bar shows the username.
- The Administration hub page (`frontend/src/app/admin/page.tsx`) was not updated. That file is outside the allowed list. The sidebar is the navigation.
- A 401 does not redirect for `POST /auth/login`, or when the detail is `current password is wrong`.
- Cookie mode is an origin comparison, not `NODE_ENV`.
- Below 1100px the "Refreshed … UTC" clock is visually clipped so search, the signed-in name and Sign out fit the 56px header. `globals.css` was not edited. The clock is still in the accessibility tree, and it is visible again from 1101px up.
- The sidebar gained Users and Directory. At 1024×768, Directory sits at the end of the sidebar scroll.
- Break-it was one mutation per new test file, not every assertion.
- No commit.

## Review round 1

The account control is one button, the username, with accessible name `Account: ada`. It opens a disclosure: Signed in as, source, roles, Change password for a local account (`/account`), and Sign out. Escape closes it and returns focus. Below 1100px the clock sits under the refresh control and the header grows with it, so the clock is not clipped. The sign-in, password, and no-role screens share a 24rem centred column. A 401 is one sentence in `role="alert"` with the error tone, above the button. Directory results use words and the status pill; the reason text stays as sent. Timeout uses a content width. Hints are the small muted style and stay tied with `aria-describedby`. Usernames use the normal text tone. The row action reads Manage, with accessible name `Manage {username}`. The last-row row-header border is gone. `/admin` lists Users (`admin:users`) and Directory (`admin:auth`) after the session is known.

Fetch paths added for auth (`client.ts`: login, logout, password, users, roles, disable, enable, unlock, reset, directory get/save/test) all go through `request()`. A bodyless POST sends no `Content-Type`. That rule is already pinned in `apiHeaders.test.ts`.

### Files changed

- `frontend/src/app/tokens.css` — `--auth-column` (24rem) and `--control-num-w` (8ch).
- `frontend/src/components/auth/auth.module.css` — centred column, account menu, hint, numeric control, username tone, last-row border, refresh stack below 1100px. The clock clip is gone.
- `frontend/src/components/shell/Account.tsx` — disclosure menu.
- `frontend/src/components/shell/Account.test.tsx` — menu contents, Escape and focus return, local-dev, LDAP.
- `frontend/src/app/login/LoginForm.tsx` — product name, narrow column, 401 alert.
- `frontend/src/app/login/LoginForm.test.tsx` — the alert is one paragraph in the error tone, not a list.
- `frontend/src/components/auth/PasswordChange.tsx`, `NoRole.tsx` — the same column. The password button fills it.
- `frontend/src/components/auth/probeCopy.ts`, `probeCopy.test.ts` — TLS, bind, trust, and the one-sentence result.
- `frontend/src/app/admin/directory/DirectoryPage.tsx` — those words, shared form width, numeric timeout, hint class.
- `frontend/src/app/admin/directory/DirectoryPage.test.tsx` — words, pills, headline, hint association.
- `frontend/src/app/admin/users/UsersPage.tsx` — username tone, Manage, last-row border.
- `frontend/src/app/admin/users/UsersPage.test.tsx` — the button name is `Manage {username}`.
- `frontend/src/app/admin/page.tsx`, `AdminIndex.tsx`, `page.test.tsx` — Users and Directory, gated. The five-section case stays for an unknown session.
- `frontend/src/app/account/page.tsx`, `AccountHome.tsx`, `AccountHome.test.tsx` — local password change; an LDAP account is told to use the directory.

### What the PNGs show

30 captures, every one `scrollWidth === clientWidth`, and no top-bar text element with `scrollWidth > clientWidth`. `captures 30 overflow 0`. Shell pages are 15px under the viewport because of the vertical scrollbar.

- Login, idle, 1440: "Behavior Analytics" above "Sign in", a narrow centred column, the button the width of that column. No shell.
- Login, 401, 1024: the same column. One sentence, "Invalid username or password", in a dark red alert above the button. Not a bullet.
- Must-change, 1440, and no-role, 1024: the same narrow centred column. No navigation. The required sentences are unchanged.
- Shell, account menu open, 1024: search shows "Search sources, rules, anomalies" in full. Under Refresh, "Refreshed 12:37:13 UTC" is complete. The button reads "ada". The menu reads "Signed in as ada", "Source: LOCAL", "Roles: admin", "Change password", "Sign out".
- Shell, account menu open, 1280 and 1440: the clock is one line beside the refresh control, "Refreshed 12:37:13 UTC", unclipped. Search shows the placeholder and Ctrl+K. The same menu.
- These admin pages do not publish a data-to time, so "Data to" is absent. The clock line that is present is the longer of the two ("Refreshed …") and it fits.
- Users, 1024: ada, bea, cy, dee in the normal text tone. Each action is "Manage" on one line. No hairline under only the username after dee.
- Directory, saved: URL, bind template and search base share one readable width. Timeout is a short field holding 5. "Optional. Empty sends no search base." is smaller and dimmer than its label.
- Directory, TLS failed: "Connection failed at TLS." TLS pill "Failed" then "certificate verify failed". Trust "System trust store". Bind pill "Not attempted" then "TLS failed". Latency "18 ms". No raw `failed`, `not_attempted`, or `system`.
- Directory, success: "Connected and bound as ada." TLS and bind pills "OK". Trust "Uploaded CA certificate". Subject, issuer, expiry `2027-01-02 03:04:05 UTC`, latency "24 ms".
- Administration, 1024: after IPS sources, Users and Directory, each with its one-line description.

### Break-it

Each mutation failed (exit 1), then the original text was restored.

- `LoginForm.tsx` — dropping `alert: true` failed "shows a 401" (`alert.tagName` was not `P`).
- `Account.tsx` — leaving the menu open failed "closes on Escape".
- `probeCopy.ts` — label `failed` failed "names a TLS failure".
- `DirectoryPage.tsx` — removing `aria-describedby` failed "describes it with the hint".

### Gates

From `frontend/`, `~/.local/node/bin` on `PATH`. Backend was not touched.

```
added 470 packages, and audited 471 packages in 4s
```

```
> qradar-observability-frontend@0.1.0 lint
> next lint
✔ No ESLint warnings or errors
```

```
> qradar-observability-frontend@0.1.0 typecheck
> tsc --noEmit
```

Exit 0. An earlier run failed `TS2322` on `link.textContent` in `page.test.tsx`; the map now uses `?? ""`, and typecheck was run again.

```
 Test Files  278 passed (278)
      Tests  3129 passed (3129)
   Duration  54.67s (transform 39.92s, setup 216.08s, import 101.18s, tests 337.52s, environment 434.46s)
```

`src/app/admin/page.test.tsx` again after the type fix:

```
 Test Files  1 passed (1)
      Tests  9 passed (9)
```

```
> qradar-observability-frontend@0.1.0 build
> next build
✓ Compiled successfully in 6.6s
✓ Generating static pages (31/31)
```

Exit 0. This build has `NEXT_PUBLIC_API_BASE_URL` unset. `.next` does not contain `127.0.0.1:8102`. The screenshot build before it compiled in 26.3s with the fixture base and also generated 31 pages.

Processes stopped by PID: fixture `859467`, `next start` `859468`, `next-server` `859492`. Capture Chrome `860224` was sent SIGTERM by the script. Nothing of this task is listening on 3102, 8102, or 9102.

No commit.

## Review round 2

A tinted banner now uses the text colour paired with that tint. `.banner.crit`, `.banner.warn` and `.banner.ok` set `--crit-text`, `--warn-text` and `--ok-text`. The base `.banner` colour stays the muted text, and a `.banner strong` heading stays the primary text. The test-connection description uses the same gap the page header leaves under its description, `--sp-6`. A hint under a field keeps only the small top margin.

### Files changed

- `frontend/src/app/globals.css` — each banner tone sets its paired text colour.
- `frontend/src/lib/contrast.test.ts` — `.banner.crit`, `.banner.warn` and `.banner.ok` must set `color: var(--<tone>-text)`.
- `frontend/src/components/auth/auth.module.css` — `.sectionNote` is `margin-bottom: var(--sp-6)`.
- `frontend/src/app/admin/directory/DirectoryPage.tsx` — the test-connection description uses that class. The search-base hint does not.
- `frontend/src/app/admin/directory/DirectoryPage.test.tsx` — the description carries both classes, the rule is `--sp-6`, and the field hint does not.
- `frontend/src/app/login/LoginForm.test.tsx` — the 401 alert is a `p` with classes `banner` and `crit`.
- `frontend/src/components/auth/PasswordChange.test.tsx` — a mismatched confirmation is `banner warn`.

### What the PNGs show

15 captures, every one `scrollWidth === clientWidth`. `captures 15 overflow 0`. The directory pages are 15px under the viewport because of the vertical scrollbar (1425/1425, 1265/1265, 1009/1009). Login, must-change and no-role match the viewport.

`/home/efe/Documents/QRadar-Dash-work/auth2/screenshots/` — `login-401`, `must-change`, `no-role`, `directory-tls-failed`, `directory-success`, each at `1440x900`, `1280x800` and `1024x768`.

- Login, 401, all three viewports: "Invalid username or password" is the pink error text on the dark red fill, the same pairing as the Failed pill. The glyphs sit on `--crit-text`, not the muted text.
- Must-change, all three: the required sentence and the three fields. This screen has no alert until a change fails. A failure is `banner warn` and a success is `banner ok`, so both use the paired text. The heading inside the banner stays the primary text.
- No-role, all three: a surface notice, not a tinted fill. "The account norole exists and has no role." is the primary text. The following sentence is the muted text on the surface, which the contrast suite already pins at 4.5:1.
- Directory, TLS failed and success, all three: under "Test connection", "The username and password are sent…" has a clear gap above "Test username". On the 1280 TLS shot the description ink ends near y=378 and the label starts near y=412. The field hint under the search base stays tight to its field.

### Break-it

Each mutation failed (exit 1), then the original text was restored.

- `globals.css` — dropping the three `color` lines failed "pairs each banner tone" (the crit block had no colour).
- `DirectoryPage.tsx` — dropping `sectionNote` from the test description failed "separates the test description".
- `auth.module.css` — `margin-bottom: 0` on `.sectionNote` failed the same test.
- `DirectoryPage.tsx` — putting `sectionNote` on the search-base hint failed "keeps a short field".

### Gates

From `frontend/`, `~/.local/node/bin` on `PATH`. Backend was not touched.

```
> qradar-observability-frontend@0.1.0 lint
> next lint
✔ No ESLint warnings or errors
```

```
> qradar-observability-frontend@0.1.0 typecheck
> tsc --noEmit
```

Exit 0.

```
 Test Files  278 passed (278)
      Tests  3131 passed (3131)
   Duration  29.64s (transform 27.53s, setup 112.94s, import 61.11s, tests 169.16s, environment 243.14s)
```

```
> qradar-observability-frontend@0.1.0 build
> next build
✓ Compiled successfully in 8.9s
✓ Generating static pages (31/31)
```

Exit 0. This build has `NEXT_PUBLIC_API_BASE_URL` unset. `.next` does not contain `127.0.0.1:8102`. The screenshot build before it compiled in 6.8s with the fixture base and also generated 31 pages. A first official rebuild still had that URL in `.next/cache` from the screenshot build, and nowhere else. `.next` was removed and the build was run again.

Processes stopped by PID: fixture `892781`, `next start` `891829`, `next-server` `891846`. Capture Chrome `893081` was sent SIGTERM by the script and was already gone. Nothing of this task is listening on 3102, 8102, or 9102.

No commit.

## Review round 3

`GET /users/roles` is still name and permissions only, sorted by name. The expected names and permission lists now come from `_ROLES` in `app/services/seed.py`, so `detection_engineer` is included and a later seeded role is too.

### Files changed

- `backend/tests/integration/test_user_roles_api.py` — the role names are `sorted(_ROLES)`, and each row's permissions equal that role's seed list. The response keys stay `name` and `permissions`. The route was not changed.

### Break-it

`get_roles` returned every role except the last. The test failed (exit 1): the body was `admin`, `analyst`, `detection_engineer`, and `viewer` was missing. The route was restored. `git diff` on `backend/app/api/routes/users.py` is empty.

### Gates

From `backend/`, `PYTHONPATH=.`, venv `/home/efe/Documents/QRadar-Dash/backend/.venv`. `TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_auth2`.

```
All checks passed!
```

`ruff check app tests`. Exit 0.

```
Success: no issues found in 240 source files
```

`mypy app`. Exit 0.

`pytest -q tests/integration/test_user_roles_api.py tests/integration/test_auth_api.py` printed 19 dots and exited 0. `-q` hides the passed footer. `--collect-only` reports 18 tests in `test_auth_api.py` and 1 in `test_user_roles_api.py`.

No commit.
