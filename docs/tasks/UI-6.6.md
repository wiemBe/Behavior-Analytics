# Task UI-6.6 — Phase 6 QA follow-ups: the findings of UI-6.1 and UI-6.2

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative), `frontend/src/app/tokens.css`, and the findings
sections of `docs/tasks/UI-6.1.report.md` and `docs/tasks/UI-6.2.report.md`. Each point below is
one finding from those reports or from the orchestrator's review. Fix each, test it, break-it
each test, and capture before/after.

## The points

1. **`.nowrap` loses on the outage policy** (UI-6.1). On `/admin/outage-policy` the
   `<time class="cell-sub nowrap">` computes `white-space: normal`, because
   `.fleet-table td .cell-sub` in `globals.css` (specificity 0,2,1) beats `.nowrap`. Make
   `.nowrap` win wherever it is used inside those tables (the proposed rule:
   `.fleet-table td .nowrap { white-space: nowrap; }` after the `.cell-sub` rule), and check every
   other table rule in `globals.css` that sets `white-space` on a cell child for the same
   collision. Extend the existing `.nowrap` contract test if one exists (`nowrapContract.test.ts`
   or similar) rather than writing a parallel one; adding assertions is allowed, changing existing
   ones is not.
2. **Empty band under a saved dashboard's title** (orchestrator, measured by UI-6.1 on
   `QRadar-Dash-work/ui59/png/after4/p9-detection-health-1024x768.png`): a saved dashboard with no
   badge ("Night shift") has ~53 px of empty space between the title and its description. The
   cause named in the report: `HomeDashboard.tsx` always passes a `meta` fragment into
   `PageHeader`, plus `.home .page-meta { min-height: var(--sp-6); }` in `widgets/dashboard.css`
   and the `margin-top` on `.page-meta` / `.page-desc`. An empty meta row must take no space;
   a template's badge row keeps its place. Check 1440, 1280 and 1024.
3. **The dashboard description is clipped with "…" at 1024** (orchestrator, seen in
   `QRadar-Dash-work/ui62/png/dialog-focus-1024x768.png`: "Geography is left out until its map
   is…"). `.dg-template-desc` clamps to two lines so the line "never moves the grid below it".
   Below the customize breakpoint (≤ 1080 px) the grid is not editable, so that reason does not
   hold there: let the description wrap in full at those widths. Above it, keep the clamp, and
   make the full text reachable to a sighted user (for example a `title` with the full text), not
   only to a screen reader.
4. **Targets under 24×24 px** (UI-6.2, WCAG 2.5.8), the non-inline ones it listed: the anomaly
   technical-detail disclosure (47.4×21), the anomaly filter checkbox label (19 px high), the
   WAF/IPS mapping radios and the classification toggles (19.5 px high labels), and the Next
   pagination link (28.7×19.5). Bring each control's hit area to at least 24 px high using the
   existing spacing tokens (padding on the label or link, `min-height` with a token), without
   changing the type size. Fix it in the shared control (`components/ui/*`, `controls.css`) when
   that is where the size comes from, so every page gains it. Measure each on the live page
   before and after and list the numbers.
5. **Lifecycle pills still title case on Home** (UI-5.9 open point): `components/home/
   RecentAnomalies.tsx` and `components/home/TopSources.tsx` render the state pill through
   `stateLabel` ("Open"). Use `statePillText` (upper case, as the backend names it), as every
   other pill does since UI-5.9. Chart labels elsewhere stay as they are.
6. **Outage policy, `summary-403`** (UI-6.1): the notice says "The source counts are not shown"
   while the Firewall row still shows 12 under "Monitored sources covered" (that number is
   `governed_monitored_sources` on the saved policy row, which is still known). Make the notice
   say precisely what is missing (the fleet summary's per-class counts and the override count),
   so it does not contradict a number that is shown. Change the copy only.

## Not in this task

The dashboard grid's `--dg-*` custom properties (the documented inline-style exception), the
shell's "Unknown" status pill semantics, fixture residue (`READY`), and the npm advisories.

## Screenshots and fixture

Reuse the merged fixture of `QRadar-Dash-work/ui61/` (it serves every route and honesty state;
see its `README` or `capture.mjs`), and for points 2 and 3 the dashboards fixture of
`QRadar-Dash-work/ui59/` or `ui62/` (a saved dashboard with no badge, and one with a long
description). Copy them into `/home/efe/Documents/QRadar-Dash-work/ui66/`. Ports 3166/8166/9166.
Capture before and after for every point at 1440×900, 1280×800 and 1024×768, **open them**, and
say which you looked at. Nothing under `/tmp`.

## Allowed files

- `frontend/src/app/globals.css` (points 1, 2, 4: only the rules each point names or needs)
- `frontend/src/widgets/dashboard.css`, `frontend/src/components/home/HomeDashboard.tsx` (2, 3)
- `frontend/src/components/ui/*`, `frontend/src/components/ui/controls.css`, and the components
  that render the listed controls (4)
- `frontend/src/components/home/RecentAnomalies.tsx`, `TopSources.tsx` (5)
- `frontend/src/app/admin/outage-policy/**`, `frontend/src/lib/outage.ts` (6)
- new tests; existing test assertions only where a point changes the pinned output (pill case,
  the notice copy): list every existing assertion you change
- `docs/tasks/UI-6.6.report.md` (new)

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim (AGENTS.md §6).
Stop every process you start by PID. Do not commit.
