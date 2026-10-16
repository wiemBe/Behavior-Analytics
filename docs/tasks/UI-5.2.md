# Task UI-5.2 — anomaly triage and investigation: review against the redesign and fix

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative: §3, §4, §5, §6, §8, §9, §10),
`frontend/src/app/tokens.css`, `frontend/src/components/ui/controls.css`, and in
`docs/UI-REDESIGN-PLAN.md`: **UR.1** (its "Rule for every entry" and the `/anomalies` and
`/anomalies/[id]` entries of **UR.1.1**), **UR.2** (UR.2.4 severity pill above all), **UR.3.3**,
**UR.7 Phase 5**, **UR.8.1** and **UR.8.6**.

**Method, test rules and the review rulings are those of `docs/tasks/UI-5.5.md`** (sections
"What restyle means here", "Rulings from UI-5.6's review that apply here", "Method" and
"Tests"): read it and follow it, substituting this task's routes, files, ports and paths.
`docs/tasks/UI-5.6.report.md` shows the expected depth of the defect list.

`/anomalies/[id]` is the product's flagship page (guidelines §3): the analyst decides there.
Hold it to the highest bar.

## Routes and states

- `/anomalies`: all seven lifecycle states; `SUPPRESSED` neutral; evidence status per row;
  filters (subject kind log source / WAF / IPS); paging; empty with and without a filter; failed
  read. The WAF and IPS variants (`waf.page.test.tsx`, `ips.page.test.tsx`).
- `/anomalies/[id]`: a log-source volume incident, a WAF incident and an IPS incident; evidence
  `UNAVAILABLE` vs `FAILED` vs empty `COMPLETE`; `TRUNCATED` withholding counts; a still-running
  incident (no end time, no duration); a resolved one; 404; failed reads.

## Known defects to fix (plus everything your review finds)

- **`frontend/src/components/Evidence.tsx`** (shared with `/alerts/[id]`): inline styles (§3a
  bans them) and values with no units ("Observed 182.4", "Confidence 0.94"; seen in UI-5.6's
  `/alerts/[id]` captures). Move the styles to a stylesheet; give each value its unit or scale
  (EPS, z, samples, a 0–1 or % confidence, whichever the payload means: read the backend to be
  sure), numbers right-aligned with tabular figures. `/alerts/[id]` must still render it
  correctly: capture it before and after. UI-5.6's alert-detail test excludes `.alert-evidence`
  from its "no inline style" check for this reason; leave that test unedited.

## Pinned tests (UR.1.1 "Keeps")

`app/anomalies/page.test.tsx`, `waf.page.test.tsx`, `ips.page.test.tsx`,
`app/anomalies/[id]/page.test.tsx` (and its WAF/IPS siblings), `AnomalyFilters*.test.tsx`,
`IncidentHeader`, `IncidentMetrics`, `EvidenceBanner`, `DimensionExplorer`, `LifecycleTimeline`,
`ContributorTable`, `StatusBadge`, `TechnicalDetails` tests, and the `error`/`loading` tests under
`app/anomalies/`. They stay green **unedited**. If a fix needs an edit to one, it is the wrong
fix: stop and report it.

## Screenshots and fixture

- Fixture API in `/home/efe/Documents/QRadar-Dash-work/ui52/fixture/` with the **backend's real
  payload shapes**. Start from a copy of
  `/home/efe/Documents/QRadar-Dash-work/t087/fixture/fixture_api.py` (anomalies, IPS) and
  `/home/efe/Documents/QRadar-Dash-work/t090/fixture/` (WAF details, metrics, baselines); do not
  edit those in place. For `/alerts/[id]`, copy what you need from
  `/home/efe/Documents/QRadar-Dash-work/ui56/fixture/`.
- Ports 3152/8152/9152. Headless Chrome is `/usr/bin/google-chrome`.
- PNGs under `/home/efe/Documents/QRadar-Dash-work/ui52/png/{before,after}/`. Nothing under
  `/tmp`.

## Allowed files

- `frontend/src/app/anomalies/**` page files and colocated stylesheets (tests: new tests only)
- `frontend/src/components/behavior/Incident*`, `Evidence*`, `Dimension*`, `Lifecycle*`,
  `Contributor*`, `AnomalyFilters*`, `AnomalyRows.tsx`, `AnomalyTabs.tsx`, `TechnicalDetails*`,
  `TopContributors.tsx` (new tests only)
- `frontend/src/components/Evidence.tsx` and a colocated stylesheet; a new
  `Evidence.test.tsx`
- `frontend/src/app/tokens.css` (new tokens only, each justified in the report)
- `docs/tasks/UI-5.2.report.md` (new)

Not `globals.css`, not `components/ui/*`, not `components/waf/*` (UI-5.3), not other pages: if a
shared component is the cause, list it as an open point.

## Gates

From `frontend/` with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim into the report
(AGENTS.md §6 format), with the defect list, the before/after PNG lists and what changed per
route. Do not commit. Stop every process you start, by PID. Never `pkill -f` or `killall`.
