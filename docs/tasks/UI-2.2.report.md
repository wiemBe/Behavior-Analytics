# UI-2.2 report: Home KPI row

## 1. Files changed
- `frontend/src/components/home/KpiFrame.tsx` (new): shared card frame (title with window, drill-down link in every state, loading / forbidden / error / refresh-failed, "Received HH:MM UTC"), `Figure` (value + unit, or em dash + reason), `Qualifiers` (INSUFFICIENT_DATA, PARTIAL, TRUNCATED, NOT_COLLECTED in words).
- `KpiTotalEps.tsx`, `KpiLogSources.tsx`, `KpiOffenses.tsx`, `KpiAnomalies.tsx`, `KpiRow.tsx` (new) and one `*.test.tsx` each; `KpiTestStates.tsx` (new, test builders; matches the `Kpi*.tsx` glob).
- `frontend/src/components/home/Sparkline.tsx` + test (new): inline SVG, no fill, no markers, gaps for null steps, text equivalent.
- `frontend/src/components/home/kpi.css` (new): all values are tokens. One literal: the 900 px / 480 px container-query widths (a query cannot read a custom property).
- `frontend/src/app/tokens.css`: `--kpi-spark-h`, `--kpi-spark-stroke` added.
- `frontend/src/lib/api.ts`: `FleetEpsSeries` types and `homeReads.epsSeries(window, groupBy)`.
- `frontend/src/lib/home/sparkline.ts` + test (new): pure path geometry (null = break in the path, lone step = own subpath).
- `frontend/src/lib/home/combine.ts` + test (new): `combine2` folds two `ResourceState`s (forbidden, then error, then loading, else ok with the older `receivedAt` and any `refreshFailed`).
- Temporary preview route `frontend/src/app/preview-kpi/page.tsx`: created for capture and deleted; not in the diff, build passes without it.

## 2. What I did
- Four cards of four structures: Total EPS (figure, expected with coverage, largest class, sparkline); Log sources (monitored of total, "n of m judged normal", reused `HealthDistribution`, silent count with its definition); Offenses (figure plus a critical/below/unknown/unassigned/SLA fact list); Anomalies (by type list plus by severity list). Each links to its drill-down (`/fleet`, `/behavior/sources`, `/offenses?status=OPEN`, `/anomalies?active_only=true`).
- All numbers come from the `lib/home/` derivations; components only format. No delta without a baseline; "observed vs expected" is the derivation's like-for-like figure.
- Secondary reads degrade, the card stays: sparkline (own error text), `/overview` total (own note, incl. permission), offense probe (null, so the derivation fails closed; while the probe is in flight the card is "loading", never a zero), active list (severity "unavailable", not an empty list).
- Row is a CSS container: 4 across, 2x2 below 900 px of row width, 1 column below 480 px. No page overflow at any viewport (measured, below).

## 3. Gates (from `frontend/`)
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, no output, exit 0
- `npm test`: ` Test Files  119 passed (119)` / ` Tests  1803 passed (1803)`
- `npm run build`: ` ✓ Compiled successfully in 6.0s`, ` ✓ Generating static pages (25/25)`

Break-it checks (each made tests fail; code restored after each):
1. `sparkline.ts`: null no longer treated as a gap: 5 sparkline tests, 2 Sparkline tests and the KpiTotalEps trend test fail.
2. `KpiFrame`: refreshFailed ignored: the 4 "keeps the data on a failed refresh" tests fail.
3. `KpiOffenses`: no wait for the probe: "waits for the probe rather than showing a zero" fails.
4. `KpiAnomalies`: a failed active list treated as listed-and-empty: "severity breakdown is unavailable, not empty" fails.
5. `KpiTotalEps`: null mean rendered as 0: "em dash and the reason, never 0" fails (plus 1 other).
6. `KpiLogSources`: bar total forced to 0: the main log-sources test fails.
7. `combine2`: forbidden ignored: combine test and "names the permission when refused" fail.
8. `KpiFrame`: HTTP status dropped from the error text: 3 error-state tests fail.
(During runs 3, 4, 7 the existing `PropertyInput > renders and filters 500 properties without a cap` also timed out at ~5.3 s; it passes in the clean full run. It is slow under machine load, not related to this task.)

Screenshots, 1440x900 / 1280x800 / 1024x768 each (fixture API on port 8122, Next on 3122, scripts under `.../scratchpad/ui22/`, outside the repo). `document.scrollWidth == clientWidth` held in all 15. Every PNG viewed.
- ok: /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/ok/kpi-1440x900.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/ok/kpi-1280x800.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/ok/kpi-1024x768.png
- partial / insufficient (most sources unjudged, partial intervals, truncated severity 200 of 345): /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/partial/kpi-1440x900.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/partial/kpi-1280x800.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/partial/kpi-1024x768.png
- no offense snapshot: /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/nosnap/kpi-1440x900.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/nosnap/kpi-1280x800.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/nosnap/kpi-1024x768.png
- forbidden on the offenses card: /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/forbidden/kpi-1440x900.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/forbidden/kpi-1280x800.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/forbidden/kpi-1024x768.png
- backend down: /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/down/kpi-1440x900.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/down/kpi-1280x800.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/down/kpi-1024x768.png

## 4. Open points
- Fixed in review of my own screenshots: card headings inherited `.main h2/h3` margins and size; overridden at higher specificity in `kpi.css`.
- Cards are tall (about 570 to 620 px at 1280 to 1440) because the row stretches to the tallest card (Anomalies); the Offenses card has empty space under its list. UI-2.5 may want to cap or reorder.
- `activeAnomalies()` (UI-2.1) has no `subject_kind` filter, so once WAF anomalies exist the severity counts could include them while `summary.open_anomalies` may not. Not changed (outside this task); worth a check against `/anomalies/summary`.
- Percent deviation prints a hyphen-minus ("-3.9 %"), as `formatPercentDelta` does elsewhere.
- Container queries are used for the fold; supported by the Chrome used for capture.

## Review fixes (round 2): compact cards

Files changed in this round: `KpiFrame.tsx`, the four `Kpi*.tsx` cards and their tests, `Sparkline.tsx` (+ test unchanged in assertions), `kpi.css`, `tokens.css` (`--kpi-spark-h` 20px), new `lib/home/format.ts` + `format.test.ts` (`signedPercent`, true minus U+2212; `formatPercentDelta` untouched).

1. **Height.** The grid is `align-items: start`; each card is as tall as its own content. Measured at ok state (cards Total EPS / Log sources / Offenses / Anomalies):
   - 1440x900: 182 / 199 / 153 / 174 px
   - 1280x800: 182 / 221 / 172 / 189 px
   - 1024x768 (2x2): 162 / 177 / 138 / 137 px
   Log sources is 221 px at 1280 (legend wraps to four rows at that card width), about 20 px over the target; all others are under 200. Worst case in any scenario: Total EPS in the partial state, 230 px at 1440/1280 (two extra qualifier lines).
2. **One structure:** title row (short visible link text "Fleet" / "Sources" / "Offenses" / "Anomalies", full accessible name kept via `aria-label`), figure with unit, one qualifier line (pill + short count, e.g. "Partial 720 of 864 intervals"), inline breakdown lines joined by a middle dot. Total EPS: "expected 1,200.00 EPS · −3.9 %" and "baselines 8 of 59 sources · largest: Firewall 54 %", then the sparkline. Log sources: "41 of 51 judged normal · 2 silent", the bar, inline legend. Offenses: "6 critical (≥ 7) · 20 below · 1 unknown", "9 unassigned · 4 over SLA". Anomalies: "6 spikes · 4 drops · 2 silent · 2 candidates · 2 recovering" plus the muted "n sources not judged" line.
3. **Explanations** moved to `title` and to the card's accessible description (`aria-describedby`, sr-only text): mean over 24 h, silent definition, critical threshold, "compared on baselined classes only", the sparkline's steps/peak text. Visible: Partial, Not collected, Insufficient data, Truncated (no longer on any card), "n not judged", "n class not in sum", the expected figure with its coverage.
4. **"Received HH:MM UTC" footer removed.** The card speaks only on a failed refresh. Stale-data detection is not implemented: the card has no cadence to compare against (UR.4.4 "older than 2 cadences" needs it); flagged below.
5. **Numbers:** EPS uses `formatEpsValue` from `lib/fleet.ts` (same as /fleet); negative percentages use U+2212 via `signedPercent`.
6. **Tests.** No state or honesty assertion dropped; moved ones are asserted in `title` / accessible description. Deleted (severity list, which is removed from the card, as allowed): "labels the severity breakdown Truncated when the list is capped", "says the severity breakdown is unavailable, not empty, when the list fails"; the severity part of the first KpiAnomalies test and the active-list read were removed too. Added: KpiAnomalies "does not read the capped active list and draws no severity list" and "says nothing about when the data arrived while all is well"; `format.test.ts`; KpiTotalEps asserts the tooltip, description, true minus; KpiLogSources asserts the silent definition in title and description.
   Break-it re-runs (each failed tests, then restored): description not wired (3 fail); silent definition title removed (1 fail); footer always on (1 fail); null mean as 0 (1 fail); probe wait removed (1 fail); true minus replaced by hyphen (1 fail, `format.test.ts`).
7. **Capture.** Temporary route re-created, used, deleted; fixture API (8122), Next (3122) and the headless Chrome were stopped (no listener on 3122/8122/9822/9823, no fixture/Chrome process left). The "ok" fixture now has no excluded class and full intervals (the partial scenario shows the degraded card), so ok shows a compact Total EPS. Page overflow: none at any of the 15.
   - ok: /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/ok/kpi-1440x900.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/ok/kpi-1280x800.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/ok/kpi-1024x768.png
   - partial: /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/partial/kpi-1440x900.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/partial/kpi-1280x800.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/partial/kpi-1024x768.png
   - nosnap: /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/nosnap/kpi-1440x900.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/nosnap/kpi-1280x800.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/nosnap/kpi-1024x768.png
   - forbidden: /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/forbidden/kpi-1440x900.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/forbidden/kpi-1280x800.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/forbidden/kpi-1024x768.png
   - down: /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/down/kpi-1440x900.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/down/kpi-1280x800.png, /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/png/down/kpi-1024x768.png

Gates (from `frontend/`):
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, no output
- `npm test`: ` Test Files  120 passed (120)` / ` Tests  1805 passed (1805)`
- `npm run build`: ` ✓ Compiled successfully in 6.8s`, ` ✓ Generating static pages (25/25)`

Open points: Log sources is ~20 px over target at 1280 (legend wraps). Stale-data text is not implemented (no cadence available to the cards). The Offenses and Anomalies "Not collected" / "Not judged" notes are short but still a second line when they wrap at narrow widths.
