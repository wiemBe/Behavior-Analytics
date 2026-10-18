# Task PW-7b — Phase G UI follow-ups: the investigation view on the real detail, the Definition AQL, the Threshold rules tab, the widget's stale limit, the values table at width

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a above all** (the
frontend quality bar; screenshots are part of acceptance) and §2 rule 4 (AQL is shown read-only,
never accepted). Then read `docs/PRODUCT-UX-GUIDELINES.md` (normative), `frontend/src/app/tokens.css`
and `docs/PHASE-G-PROPERTY-WATCH.md` §G.13.1–§G.13.5 (binding).

**Base:** the merged Phase G tree (PW-7, PW-8, PW-9 on the UI side; PW-5c and PW-6b on the API
side). The API shapes are the code: read `backend/app/schemas/anomaly.py` (`PwValueRefOut`,
`PwDetectionDetailOut`), `backend/app/schemas/property_watch.py` (`WatchAqlOut`,
`PropertyWatchOut.cadence_seconds`, `threshold_rules`) and the PW-5c, PW-6b, PW-7, PW-8 and PW-9
reports. **No backend change in this task**: if the UI needs one, stop and report.

## Build

1. **Investigation view on the real detail** (`components/propertywatch/PwInvestigation.tsx`,
   `app/anomalies/[id]/page.tsx`; PW-7 open points 1 and 7, PW-5c "differences for the UI"):
   - read `GET /anomalies/{id}` as PW-5c serves it; remove every fixture-only assumption;
   - `pw_value.value_id` links to `/behavior/watches/{watch_id}?value={value_id}`; when it is
     null, say "this value is no longer stored (retention); the incident is kept", with no link;
   - `detection`: the band from `expected_low`/`expected_high` as a band (never three lines), the
     observed count with its measurement (`LOWER_BOUND` reads "at least n"), the threshold and
     the baseline sample count; a missing figure is "not recorded", never 0;
   - `explanation_package` null: no empty panel;
   - transitions oldest first, each with its UTC time and reason.
2. **Units on the new-value figures** (PW-5c: "the detector declares no unit"). Read
   `backend/app/property_watch/detectors.py` and `capabilities.py` and label each new-value and
   new-combination figure with what it counts (events, buckets, days). Say in the report, per
   field, where in the code you found its meaning. A field whose meaning the code does not settle
   is shown with its name and "count", and listed as an open point.
3. **Definition tab, the generated AQL** (§G.13.3, PW-7 open point 5) from
   `GET /property-watches/{id}/aql`: `current` in a collapsed, read-only `CodePanel` rendered as
   text, labelled "Generated query text. The collector adds the time range and the page limit
   when it runs." with the window it was generated for; `current_unavailable` shown as its reason
   instead; `last_bucket` texts with that bucket's UTC time and `watch_version`, or "no bucket
   collected yet". Pinned values may appear in the text (D-22).
4. **Threshold rules tab** (§G.13.3) from `threshold_rules` on the single-watch read: each rule
   with its name (link to `/custom-rules/{id}`), the pinned value it watches, and enabled or
   disabled; an empty tab says no rule pins a value of this watch and links to
   `/custom-rules/new` with the PW kind preselected if PW-8's picker supports that, else to
   `/custom-rules/new`. A reader without `custom_rule:write` (check the real permission name in
   `backend/app/security/rbac.py`) sees the list without the create link and is told which
   permission adds it.
5. **The widget's stale limit** (PW-9 open point; §G.13.5 "older than twice the family
   cadence"): replace `PW_STALE_AFTER_SECONDS = 2 * 3600` in `widgets/pwWidgetState.ts` with
   `2 * cadence_seconds` from the watch read. The backend's own `STALE` status still counts.
   Pin with tests at a 5-minute, a 1-hour and an 8-hour cadence (an 8-hour watch 3 hours old is
   not stale).
6. **The values table at width** (from the PW-8 review PNGs, `WatchValuesTable.tsx`):
   - a long value (the 120-character service account in the fixtures) takes at most two lines,
     cut with an ellipsis; the full text stays reachable as text (a `title` is not enough for
     keyboard users: a disclosure or a focusable element that shows it). It is attacker-controlled
     text: rendered as text, never markup;
   - at 1024×768 the table's last column ("Baseline") is clipped. Make every column visible at
     the three viewports without page-level overflow (guidelines §9): drop or merge a column at
     narrow width, or wrap the header; say which and why.
7. `[BUILT — PW-7]` markers in `docs/PHASE-G-PROPERTY-WATCH.md` §G.13.1–§G.13.3 (PW-7 open point
   3), plus `[BUILT — PW-7b]` where this task completes the design.

## States and tests

Every state of each item: loading, error (what failed, what to do), forbidden (the permission
named), empty, and the honesty states. Vitest covers each; break each once and report which test
failed. Values and AQL text are rendered as text; a test pins it with a hostile value.

## Screenshots

`/anomalies/{id}` for a PW spike (band, exact) and a PW new value (lower bound, value deleted),
the watch page's Definition tab (AQL open; and `current_unavailable`), the Threshold rules tab
(with rules; empty; reader), the values table with the long value, and the dashboard widget at an
8-hour cadence, at 1440×900, 1280×800 and 1024×768, with headless Chrome against a deterministic
fixture API serving the **real** payload shapes (read them from the schemas). Fixture, capture
script, PNGs and scratch under `/home/efe/Documents/QRadar-Dash-work/pw7b/`, never `/tmp`. Open
the PNGs and say what you saw; list their paths; no page-level horizontal overflow.

## Allowed files

- `frontend/src/components/propertywatch/**`, `frontend/src/app/behavior/watches/**`,
  `frontend/src/app/anomalies/[id]/**` (the PW branch only)
- `frontend/src/widgets/pwWidgetState.ts`, `frontend/src/widgets/PropertyWatchWidget.tsx` and
  their tests
- `frontend/src/lib/api.ts` (property-watch and PW anomaly types and calls only)
- new tests; `docs/PHASE-G-PROPERTY-WATCH.md` (markers only); `docs/tasks/PW-7b.report.md` (new)

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm ci`, `npm run lint`,
`npm run typecheck`, `npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim.
Stop every process you start by PID. Do not commit.
