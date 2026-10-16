# Task UI-5.9 — cross-page consistency: the open points Phase 5 collected

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative), `frontend/src/app/tokens.css`,
`frontend/src/components/ui/controls.css`, and the review rulings in `docs/tasks/UI-5.5.md`
("Rulings from UI-5.6's review that apply here") and `docs/tasks/UI-5.6b.md`.

Phase 5 restyled every page route by route. Each route task left open points that sit in **shared**
code it was not allowed to touch. This task fixes them together, so the same thing looks the same
on every page. Start only after UI-5.8 is merged (it owns the boundaries and `.nowrap`).

## The points (each: fix, test, break-it, before/after PNG)

1. **State pill case.** The WAF and IPS investigation pages render the incident state in title
   case ("Open"); the log-source investigation and every list render it in upper case ("OPEN").
   One rule for the whole app: the lifecycle state pill shows the state as the backend names it,
   upper case, through the shared formatter the anomaly list uses. (`components/waf/*`,
   `components/ips/*`; source: UI-5.2 report.)
2. **Evidence failure said three times.** On a FAILED-evidence incident `/anomalies/[id]` states
   the failure in the "What changed" notice, in `EvidenceBanner` and in the Evidence tab. Keep it
   once in full (the banner) and let the other two refer to it in a few words. The wording is
   pinned by tests: **the orchestrator authorises editing only the assertions that pin the two
   shortened sentences**; list each edit. (UI-5.2 report.)
3. **The sticky actions rule.** `.sticky-actions` in `globals.css` draws a vertical rule beside
   the Open/severity column on the anomaly tables. A pinned column needs separation only while the
   table actually scrolls under it; otherwise no rule. (UI-5.1 report.)
4. **Empty space in 2×2 primary cards at 1024.** `StatCard` grids use `grid-auto-rows: 1fr`, so a
   card without a note is as tall as one with a note. Keep the cards of one row equal height, but
   do not stretch a row to the tallest row of the grid. `shellFixes.test.ts` pins
   `.grid-4 { grid-auto-rows: 1fr }`: **stop and report** if the fix needs that assertion changed;
   do not edit it. (UI-5.1 report.)
5. **`stateMeaning` (`lib/behavior.ts`)** says "within the expected band" for a NORMAL source
   whose observed value lies outside the band (seen with inconsistent data: 0.00 EPS against
   31–48). The sentence must never contradict the numbers beside it: when the observed value is
   outside the band, say NORMAL "by the detector's verdict" without claiming it is inside the
   band. Test both cases. (UI-5.1 report.)
6. **`MostMatched` "≥ < 0.01".** A lower-bound rate below the display floor reads "≥ < 0.01".
   "≥ 0.01" would be false (the measured lower bound is below 0.01). Render either
   "< 0.01 (lower bound)" or the figure with enough precision to show it; pick one, say why, test
   it. (UI-5.5 report.)
7. **Search placeholders clipped at 1024** ("Search source name.."): shorten the placeholder
   or let the field take its width, on every filter row that clips (check all list pages).
8. **The sidebar scrolls at 1024×768.** Measure the nav at 768 px of height: if it cannot fit
   without scrolling, tighten the group spacing using tokens (no new literal) until it fits at
   1024×768 and 1280×800; text-only navigation stays (D-11).

9. **Status words in mixed case inside one widget** (seen in the UI-4.2 review,
   `QRadar-Dash-work/ui42/png/template-admin-1440x900.png`): `HealthDetection`
   (`components/catalog/HealthDetection.tsx`) lists rule verdicts as "Healthy", "Degraded",
   "SILENT", and its note reads "Other statuses: UNCOVERED 9 · Degraded 5". One case per kind of
   word, matching point 1's rule for states and the coverage page's words for verdicts. Check the
   other `components/catalog/*` widgets for the same mix. (Add `components/catalog/*` and their
   tests to the allowed files for this point.)

10. **The template line runs into its note** (seen in the T-111 review,
    `docs/screenshots/ui/dashboard-1440x900.png`): on an unsaved template the header reads
    "Template, not saved 24 h view of fleet volume, …" on one line, two sentences with no
    separator. Make "Template, not saved" a distinct element (the existing badge style, or its
    own muted line), so the description reads as its own sentence. Applies to
    `components/home/HomeDashboard.tsx` and `widgets/dashboard.css` (add both to the allowed
    files for this point). Check 1440, 1280 and 1024, where the line may wrap.

## Not in this task

Long single-word source names (a 92-character name wraps to 6 lines at 1024): accepted.

## Screenshots and fixture

Reuse the newest fixtures (`QRadar-Dash-work/ui51/`, `ui52/`, `ui55/`) by copying them into
`/home/efe/Documents/QRadar-Dash-work/ui59/`. Ports 3159/8159/9159. Capture before and after for
each point at 1440×900, 1280×800, 1024×768 and **open them**; say which you looked at. Nothing under
`/tmp`.

## Allowed files

- `frontend/src/components/waf/*`, `frontend/src/components/ips/*` (point 1)
- `frontend/src/components/behavior/EvidenceBanner*`, `app/anomalies/[id]/**` and the tests named
  in point 2 (only the authorised assertions)
- `frontend/src/app/globals.css`: only the `.sticky-actions` and `StatCard`-grid rules (points 3, 4)
- `frontend/src/lib/behavior.ts` (point 5), `frontend/src/components/rules/MostMatched.tsx` (6)
- the filter components and page files with clipped placeholders (7)
- the shell navigation stylesheet (8)
- new tests for each point; `docs/tasks/UI-5.9.report.md` (new)

## Gates

From `frontend/` with `~/.local/node/bin` on `PATH`: `npm ci`, `npm run lint`,
`npm run typecheck`, `npx vitest run --no-cache`, `npm run build`, summary lines verbatim in the
report (AGENTS.md §6). Do not commit. Stop every process you start, by PID.
