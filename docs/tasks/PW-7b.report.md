STATUS: FINAL

# PW-7b report

## Files changed
Source: lib/api.ts (types: PwValueRef.value_id, PwDetectionDetail, PwAnomalyDetail.detection, PwPinRule, WatchAql, PropertyWatch.threshold_rules/cadence_seconds, api.propertyWatchAql); app/anomalies/[id]/page.tsx (pwData uses pw_value.value_id, no lookup); components/propertywatch: PwInvestigation.tsx (rewritten), pwFormat.ts (figure units), pwChartOption.ts + PropertyWatchSeriesChart.tsx (detectionBand), WatchAql.tsx (new), ThresholdRules.tsx (new), WatchDefinition.tsx, WatchWorkspace.tsx (Threshold rules tab), WatchValuesTable.tsx + propertywatch.css, fixtures.ts; app/behavior/watches/[id]/page.tsx (aql, rule access, rule values); widgets/pwWidgetState.ts; docs/PHASE-G-PROPERTY-WATCH.md (markers only).
Tests: new WatchAql.test.tsx, ThresholdRules.test.tsx; changed pw.page.test.tsx, page.test.tsx (watch), WatchWorkspace.test.tsx, WatchValuesTable.test.tsx, pwChartOption.test.ts, pwWidgetState.test.ts, PropertyWatchWidget.test.tsx.
Existing tests edited (spec-mandated): pwWidgetState.test.ts staleness test (PW_STALE_AFTER_SECONDS removed); WatchWorkspace.test.tsx tab-list test (it asserted there is NO Threshold tab); pw.page.test.tsx (fixture-only lookup/not-found assumptions replaced); WatchWorkspace props() helper gained aql/ruleAccess.

## Decisions
- Band: one markArea rectangle (low..high) over the incident span, drawn in the series chart; no band when expected_low/high is null or for new-value types; text range always shown.
- Rule create link: PW-8's picker takes no query param, so link is /custom-rules/new. Permission custom_rule:write (rbac.py PERM_CUSTOM_RULE_WRITE). Unknown permissions: link hidden and said so.
- Rule's pinned value text: from the page's values read; if a rule's value is not there, one wider read (168 h, limit 200); still missing: "not among the tracked values read; it may no longer be stored".
- Stale: 2 x cadence_seconds; missing/non-positive cadence falls back to the 1 h family default (named constant), backend STALE still counts.
- Values table: dropped the "Baseline and incidents" column, merged under the value (all 6 remaining columns visible at 1024); value >60 chars clamped to 2 lines, full text in a details/summary.
- Legend/summary for new-value "Observed": "events" (lower bound: "at least").

## Item 2: per-field unit sources (backend/app/property_watch/detectors.py, baseline.py)
- events_since_first_seen, observed (new value): `_materiality` compares `events` with new_value_min_events and words "{events} events across {buckets} buckets": events.
- buckets_with_traffic: the same sentence: buckets.
- history_buckets_verified and baseline_sample_count (new value): `history_buckets`, `sample_count=novelty.history_buckets`: buckets.
- threshold (new value): float(new_value_min_events): events.
- threshold (volume types): thresholds.deviation_z, compared with the robust z: a z-score (shown "threshold 3.5 (z-score)", not events).
- baseline_sample_count (volume): cell sample_count = retained bucket readings (baseline.py `retained[key].append(...)`): buckets.
- Expected band / median / observed_events: events per bucket (metric_semantics, `_cell_facts`).
- expected_value 0.0 of a new value is a constant, not a measurement: not shown. No field was left as bare "count".

## Gates (frontend/)
- npm ci: "added ... 6 vulnerabilities (1 moderate, 5 high)" (completed, exit ok; log npmci.log)
- npm run lint: "✔ No ESLint warnings or errors"
- npm run typecheck: tsc --noEmit, no output (clean)
- npx vitest run --no-cache: "Test Files  313 passed (313) / Tests  3490 passed (3490)"
- npm run build: "✓ Compiled successfully in 7.9s" (plain build; the capture build used the fixture API URL)
Baseline before my work: 3445 tests.

## Break-it (each failed, then restored; whole suite re-run green)
stale fixed 2 h: 2 failed (pwWidgetState "stale past twice the watch's own cadence", widget "not stale at 3 h ... 8 h"); value link always on: "says the value is no longer stored..." ; current_unavailable ignored: WatchAql "shows the reason instead of the text..."; reader gets create link: 3 ThresholdRules tests; no disclosure: 2 WatchValuesTable tests; band not passed: "draws the recorded expected range as one band"; transitions newest first: "lists the transitions oldest first"; wrong unit on buckets: "labels a new value's figures...".

## Screenshots (/home/efe/Documents/QRadar-Dash-work/pw7b/, <name>-<WxH>.png and -full.png; fixture_api.py, capture.mjs there; real payload shapes)
Page overflow (scrollWidth vs clientWidth): none in all 27 captures (capture.log).
Opened and looked at: inv-spike-1440x900-full (band rectangle at the incident over the stored band, units on every figure, lifecycle oldest first); inv-new-value-1280x800-full (retention notice, no link, "at least 31 events", buckets/events units, no chart note instead of empty chart); def-aql-1024x768-full (AQL panel open, window UTC, last bucket UTC+version); def-aql-unavailable-1024x768-full (reason replaces text; last bucket kept); rules-1024x768 (4 rules, hostile-safe text, enabled/disabled, unresolved value wording); rules-reader-1280x800 (list, no create link, names custom_rule:write); rules-empty-1440x900 (empty text + create link); values-long-1024x768-full (all 6 columns visible, 120-char value on 2 lines with ellipsis, opened disclosure shows full text, hostile img text shown as text); widget-8h-1024x768 (bucket 3 h old at 8 h cadence: not stale, "Last bucket ... (3 h ago)").
The other viewports of each scenario (1440x900, 1280x800, 1024x768 pairs) were captured and checked for overflow numerically but not individually opened.

## Open points
1. Rule pinned-value text depends on the page's values read (+ one 200-row wide read); a rule on a value beyond that shows the hedged wording. Risk: an analyst sees "not among..." for a value that is still stored. A backend field with the value text would fix it.
2. /custom-rules/new has no PW preselect (picker takes no param). Risk: one extra click.
3. Fallback cadence 1 h when cadence_seconds is absent: an old backend with a different cadence could mislabel stale/fresh.
4. Detection band is the baseline recorded at detection drawn over the whole incident span; a long incident crossing hours of the week shows one fixed band while the stored band steps. Both are labelled.
5. Explanation package panel is minimal (status, windows, totals); PW never produces one today, so it is only unit-tested.
6. Detail GET for the AQL is a 7th parallel read on the watch page; failure is shown on the Definition tab only.
7. Capture used a production build with NEXT_PUBLIC_API_BASE_URL set to the fixture; processes stopped by PID (ports free).
