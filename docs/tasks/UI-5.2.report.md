# UI-5.2 report: anomaly triage and investigation, and the shared Evidence component

## 1. Files changed
- `frontend/src/components/Evidence.tsx`: rewritten. No inline styles. Every figure has its unit, from the evidence's own `anomaly_type`. Figures are right-aligned (`num`, tabular). A missing figure is "—" with no unit.
- `frontend/src/components/Evidence.css` (new): the layout (label, figure, unit).
- `frontend/src/components/Evidence.test.tsx` (new): 9 tests.
- `frontend/src/app/anomalies/anomalies.css` (new) and `page.tsx`: the list table fits at 1440, 1280 and 1024 (details in section 2). The failed-read text now says what to do.
- `frontend/src/components/behavior/AnomalyRows.tsx`: classes only (`fold-started`, `col-dev`, `col-started`, `fold-dev`). No text, order or behaviour changed.
- `frontend/src/app/anomalies/[id]/page.tsx` and `incident.css` (new): error branch uses `PageHeader` (one h1), says what to do, links back to the list; the headline contributors stay three across at 900 to 1080 px.
- `frontend/src/app/anomalies/list-columns.test.tsx` and `[id]/error-states.test.tsx` (new).
- No existing test, `globals.css`, `components/ui/*`, `components/waf/*` or `tokens.css` touched. No new tokens.

## 2. What I did

### Defect list (route, viewport, defect, rule, fix)
1. `/alerts/[id]`, all: `Evidence` used inline styles (AGENTS 3a). Fixed (stylesheet).
2. `/alerts/[id]`, all: figures had no unit ("182.4", "0.94"). Units, from the backend detectors (`app/anomaly/detectors.py`):
   - VOLUME_SPIKE/DROP: EPS, deviation and threshold in z.
   - NO_EVENTS: events / bucket (its baseline is the `event_count` cell). Its deviation is null and stays "—".
   - PARSING_DEGRADATION, UNKNOWN_EVENT_SPIKE: stored as a 0 to 1 share, shown as % (deviation in pp).
   - TIMESTAMP_DELAY: s (deviation is a multiple of the limit).
   - CARDINALITY_DROP: source IPs (deviation is a % below baseline).
   - COLLECTION_ERROR: errors.
   - REPEATED_PAYLOAD: intervals.
   - Confidence is a 0 to 1 score: "0.94 (0 to 1)", as `IncidentMetrics` states it.
   - A payload with no known `anomaly_type` (a scheduled-search alert) shows bare numbers, not a guessed unit.
3. `/alerts/[id]`, all: the 8-row table took 46 px per row with grid lines for 8 numbers. Now compact rows.
4. `/anomalies`, 1440 (table needed 1229 px, had 1144), 1280 (1028 vs 984), 1024 (786 vs 780): the table scrolled sideways. At 1280 the Started header was cut under the pinned actions column. Fix: cell padding 8 px up to 1440, Deviation folds under the observed figure up to 1440, Started folds under the subject up to 1280. After: scroll width equals client width at every viewport, for the mixed, WAF and IPS lists. The existing `col-md`/`col-sm` rules are untouched. The table's header "Started" is pinned by an existing test, so the cells keep "UTC" and the header does not say it.
5. `/anomalies/[id]` 404/403/401/500, all: a bare `h1.page-title` outside `PageHeader`, and notices that did not say what to do. Fixed. Existing assertions match by substring and still pass.
6. `/anomalies`, failed read: "backend may be unreachable" gave no action. Added "reload the page to try again"; a 403 names the `read:anomalies` grant.
7. `/anomalies/[id]`, 1024: the three headline contributor cards stacked into three full-width rows and pushed the evidence tabs a screen down. They share one row from 900 px.

### Checked and found fine (no change)
- List: all seven lifecycle states. SUPPRESSED and NORMAL are neutral. Evidence status per row (folded under State below 1440 px). Filtered-empty and empty notices. Pagination "1-25 of 39".
- Investigation (log source): header, summary, the four measures with units, still-running (no end, duration "—" and "Still running"), resolved, timeline with a "Time (UTC)" axis and a legend, evidence UNAVAILABLE / FAILED / empty COMPLETE / TRUNCATED, metrics failure notice.
- WAF and IPS investigations: fine in both viewports.

### Before and after PNGs
- Before: `/home/efe/Documents/QRadar-Dash-work/ui52/png/before/`
- After: `/home/efe/Documents/QRadar-Dash-work/ui52/png/after/`
- 32 states, each at `<name>-1440x900.png`, `<name>-1280x800.png` and `<name>-1024x768.png` (96 files per side):
  - list: `list`, `list-many-p2`, `list-waf`, `list-ips`, `list-filtered-empty`, `list-empty`, `list-error`, `list-suppressed`
  - investigation: `d-logsource`, `d-unavailable`, `d-failed`, `d-emptycomplete`, `d-truncated`, `d-resolved`, `d-noevents`, `d-metricsfail`, `d-waf`, `d-waf-newep`, `d-ips`, `d-ips-source`, `d-ips-trunc`, `d-404`, `d-error`
  - alerts: `alert-volume`, `alert-parsing`, `alert-delay`, `alert-noevents`, `alert-search`, `alert-errors`, `alert-repeated`, `alert-sparse`, `alert-noevidence`
- Fixture, capture script and logs are in `/home/efe/Documents/QRadar-Dash-work/ui52/`: `fixture/server.py` on `base_api.py` (t090) and `ui56_data.py`, `capture.mjs`, `start.sh`, `logs/`. All use the backend's real payload shapes.
- One difference between the sets that is not code: before/after used the same fixture, except that I fixed an invalid `evidence_status: "READY"` in the copied t090 base after the first run. The `list` states were recaptured afterwards.

### PNGs I opened, and what I saw
- Before: `list` at 1280 and 1024, `d-logsource` at 1280 and 1024, `d-failed` 1280, `d-truncated` 1024, `d-404` 1280, `list-filtered-empty` 1280, `d-waf` 1024, `d-ips` 1280, `alert-volume` 1280.
- After: `list` 1280 and 1024, `d-logsource` 1024, `alert-volume` 1280, `alert-parsing` 1024, `alert-sparse` 1280, `d-404` 1024.
- What changed on screen:
  - List: no clipped Started header, and the Open/More actions sit inside the frame, at both widths.
  - `d-logsource` 1024: the three contributor cards sit in one row and the tabs start about 250 px higher.
  - Evidence: compact rows with right-aligned figures and units ("182.4 EPS", "61.2 %", "28.8 pp"). The sparse case shows "—" with no unit for the missing fields.
  - 404: a single h1 and the back link.
- I did not open every PNG. The rest were checked by measurement only: page overflow, scroll width, inline-style count.

## 3. Gates (from `frontend/`)
- `npm run lint`: `No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit` clean, no output
- `npx vitest run --no-cache`: `Test Files  199 passed (199)` / `Tests  2635 passed | 2 todo (2637)`. The 2 todos are not mine (UI-5.5's T-096 `it.todo` lines).
- `npm run build`: succeeded.
- Break-it:
  - Evidence: removing the percent scaling and showing the unit for a missing value fails 2 tests.
  - `AnomalyRows` and the investigation error branch: removing `fold-started`/`col-dev` and the back link fails 5 tests.
  - Code restored after each.
- Computed-style measurement of overflow: scroll width equals client width on every list capture at all three viewports (`logs/after-measurements.json`). `[style]` elements are only ECharts' and Next's route announcer.
- Processes: Next (848467), Chrome (848469) and the fixture (864014) were stopped by PID. Ports 3152, 8152 and 9152 are free.

## 4. Open points
- Evidence is stated up to three times on a FAILED incident ("What changed" notice, the `EvidenceBanner`, the Evidence tab). The wording is pinned by tests I may not edit (`DimensionExplorer`, `EvidenceBanner`, the page test), so I left it.
- The WAF and IPS investigation pages (outside my files) render the state pill in title case ("Open") while the log-source page uses upper case ("OPEN"). That belongs to `components/waf` and `components/ips`.
- `IncidentMetrics` at 1024 still puts the lead "Observed" figure on its own row. That is the shared `globals.css` rule, and the WAF/IPS notes are long enough that a four-up row would wrap badly. Left as designed.
- `/alerts/[id]` delivery-table times still wrap at 1024 ("10-14 / 12:59 / UTC"). That is UI-5.6b, which owns that file.
- The evidence unit table is keyed by Phase A `AnomalyType`. WAF and IPS alerts, or a search alert, will show bare numbers until their evidence carries a known type.
