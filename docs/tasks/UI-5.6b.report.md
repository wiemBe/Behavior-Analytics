# UI-5.6b report: follow-ups from UI-5.6's review

## 1. Files changed (all under `frontend/src/`)
- `app/log-sources/qradarStatus.ts` (new, one shared tone and label function; a non-page module because a Next page file cannot export extras).
- `app/log-sources/page.tsx`, `[id]/page.tsx`, `[id]/log-source-detail.css`: shared status tones, `PageHeader` h1, "Expected interval" unit only with a number, link replaces the Phase 2 sentence, local h2 imitation CSS removed.
- `app/alerts/[id]/page.tsx`, `alert-detail.css`: `PageHeader` h1, `.nowrap` on Sent and Next-attempt, Error floor 12ch to 6ch, confirm-block styles, imitation CSS removed.
- `app/searches/[id]/page.tsx`, `components/searches/ExecutionsTable.tsx`, `lib/searchRuns.ts`: cells under a "(UTC)" header drop "UTC" (via `axisTimeLabel`, doc comment widened, now accepts null), nowrap.
- `components/ResultTrend.tsx`: `time` axis (`useUTC`), `[time, value]` data, markers by time.
- `components/AlertActions.tsx`: inline Resolve confirmation with focus handling.
- Tests: `log-sources/page.test.tsx`, `log-sources/[id]/page.test.tsx`, `alerts/[id]/page.test.tsx`, `searches/[id]/page.test.tsx`, `AlertActions.test.tsx`, `ResultTrend.test.tsx`, `ExecutionsTable.test.tsx`.
- No token added. `lib/searchRuns.ts` and `qradarStatus.ts` are not on the allowed list; both are needed (one-line reuse, shared function). Flagging.

## 2. What I did, per fix
1. Unit only with a number. Checked every "label: value unit" on the six routes: only that line was affected (the other units are on numbers or "/ 100" after a real score).
2. Placeholder replaced with "EPS trend, anomaly timeline and baseline overlay: source behavior." linking `/behavior/sources/${d.id}`. One-to-one: `/behavior/sources/[id]` calls `api.sourceBehavior(id)` and `api.logSource(id)` with the same inventory id that `/log-sources/[id]` loads, and `/log-sources` ids are what `/behavior/sources` links use (`log_source_id`).
3. Both detail pages use `PageHeader` (h1); imitation CSS removed.
4. SUCCESS ok, WARNING warn, ERROR crit, anything else and null neutral with the reported value (null "unknown"), one function for list and detail. The detail page now shows the status as a pill like the list.
5. Delivery times `nowrap`; the Error column absorbs the wrap (floor 12ch to 6ch, needed to fit). Table 765 of 765 at 1024.
6. "(UTC)" header columns: Execution history Started and Query version "When" cells drop the zone. Other tables on the six routes have no "(UTC)" header (alerts say "Times are UTC." in the description and keep the zone in cells).
7. Time axis. Tests: axis type, 4 min vs 15 min spacing, gap at truncated run, markers (version line, isolated point) as times.
8. Resolve opens "Resolve this alert? A resolved alert cannot be reopened." with "Resolve alert" and "Cancel". Focus moves to "Resolve alert", returns to Resolve on Cancel (and after a failed attempt). Acknowledge and Run now untouched.

## 3. Authorised and necessary test edits
Authorised: `alerts/[id]` and `log-sources/[id]` heading `level: 2` to `1`; the Phase-2 sentence test replaced by a link test; `AlertActions` "sends the typed resolution reason" gained one click on "Resolve alert" (nothing else).
Not named in the task but forced by it (a test pinned the old behaviour; please confirm): `log-sources/[id]` "Expected interval: — s" now `/Expected interval: —Last event/` (fix 1); `ExecutionsTable.test` start-time assertion and two `searches/[id]` assertions lost "UTC" in the cell and gained `not.toHaveTextContent("UTC")` (fix 6); six `ResultTrend.test` assertions moved from category indices to `[time, value]` data and time positions (fix 7).
New tests: 20 or so (statuses, no unit, link, h1, nowrap, time axis, confirm, focus, Acknowledge single-click).
Break-it: 13 single-line mutations (script `/home/efe/Documents/QRadar-Dash-work/ui56b/breakit.py`), all 13 failed the matching test; sources restored after each.

## 4. Gates (from `frontend/`)
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, no output
- `npx vitest run --no-cache`: `Test Files  1 failed | 187 passed (188)`, `Tests  1 failed | 2536 passed | 2 todo (2539)`. The failure is a 5 s timeout in `src/app/rules/statistics/page.test.tsx` ("shows a truncated silent list...") under full-suite load (a different timeout in `ProfileEditor.test.tsx` in an earlier run); that file passes alone (18 passed). Not in my files; not a clean full-suite run.
- `npm run build`: `✓ Compiled successfully in 9.3s`
Processes started (fixture, Next, Chrome) were stopped by PID; ports 3149/8149/9149 free.

## 5. Screenshots
Harness copy: `/home/efe/Documents/QRadar-Dash-work/ui56b/` (fixture now has a DISABLED source, a WARNING source, null expected intervals, uneven executions). PNGs, 72 files (24 scenarios x 3 viewports): `/home/efe/Documents/QRadar-Dash-work/ui56b/png/after/`, named `{route}-{1440x900,1280x800,1024x768}.png`; new: `alert-detail-confirm`, `log-source-detail-disabled`, `-nointerval`, `-warning`. Document scrollWidth equals clientWidth on all 72; every table sits inside its scroll container with no overflow (1024: delivery 765/765, executions 765/765, list tables 780/780).
Opened and looked at: `alert-detail-open-1024x768` (before the width fix, showing the 809 vs 765 overflow), `alert-detail-confirm-1024x768`, `search-detail-1024x768`, `log-sources-1440x900`, `log-source-detail-nointerval-1280x800`. The other PNGs were checked by measurement only.
Seen: list pills (SUCCESS green, WARNING amber, ERROR red, DISABLED and unknown grey); "Expected interval: —" with no unit; link line; confirmation block under the actions row; execution and version times in one line without repeated UTC; the trend line is spaced by time, with gaps at truncated runs and a "Time (UTC)" axis.

## 6. Open points
1. Delivery table at 1024: with a long dead-letter message the Error column is narrow (6ch floor) and breaks mid-word over many lines (seen in the confirm PNG). It fits without scrolling, as required, but it is ugly. A cleaner fix needs width from elsewhere (narrower Transition column) or a truncated message with the full text on hover; analyst's call.
2. The trend's threshold line is drawn across truncated gaps (the threshold is a constant, not a measurement).
3. Not run: the `ui56` before/after comparison; only after PNGs exist.

## Round 2: delivery table Error column (BLOCKED: does not fit at 1024 with a 16ch floor)
Changes: headers "Sent (UTC)" and "Next attempt (UTC)", cells use `axisTimeLabel` (no zone), nowrap; `td.cell-error` is `min-width: 16ch; overflow-wrap: break-word` (no `anywhere`/`break-all`). The table already had `table-dense`. Lifecycle lines keep "UTC".
Test edits (only `app/alerts/[id]/page.test.tsx`): header names, the delivery-time test now asserts the cell has no "UTC", my earlier nowrap test uses the bare time. New test pins the 16ch floor and `break-word` by reading the stylesheet and the `cell-error` class. Break-it: `anywhere` and `6ch` each fail it; dropping `axisTimeLabel` for `formatDateTime` fails 2 tests. All restored.

**Measured at 1024x768 (stop condition hit): table 828 px in a 765 px container (scrollWidth 828, 63 px sideways scroll inside `.table-scroll`; the page itself does not overflow, doc 1009/1009).** Column widths: Channel 97, Transition 143, Status 125, Attempts 76, Sent (UTC) 99, Next attempt (UTC) 135, Error 153. At 1280 and 1440 it fits (969/969, 1129/1129). I did not shrink the Error column. Candidates for the orchestrator: a shorter Transition column (143, "ACKNOWLEDGED" is the widest word), a wrapping "Next attempt (UTC)" header (header may wrap, cells not), Status pill padding, or drop Channel/Transition into one column.
Screenshots: `alert-detail-*` re-shot at all three viewports in `png/after/`. I did NOT open the 1024 and 1280 PNGs in round 2 (stopped on the measurement); measurements above are from the live page.
Gates: lint `✔ No ESLint warnings or errors`; typecheck `tsc --noEmit` clean; `npx vitest run --no-cache` `Test Files  188 passed (188)`, `Tests  2538 passed | 2 todo (2540)` (no timeout this run); build `✓ Compiled successfully in 74s`. Processes stopped by PID; ports free.

## Round 3: Error floor 12ch, headers wrap (STILL OVER at 1024 by 24 px; stopped)
Changes: `td.cell-error` is `min-width: 12ch; overflow-wrap: break-word`; the CSS test pins 12ch (break-it: 16ch fails it). The table has class `delivery-table`; `.delivery-table th { white-space: normal }` overrides the global `th { white-space: nowrap }` so "Next attempt (UTC)" wraps at spaces. Also `.delivery-table td.nowrap { white-space: nowrap }`: `.nowrap` is only defined globally inside a `max-width: 1200px` media query and in `controls.css` (not imported by this page), so at 1280 the Sent cell wrapped ("10-14 / 12:59") in my first shot; fixed and re-shot. The same latent gap may affect `.nowrap` cells on /alerts and /log-sources between 1201 px and wide screens when space is tight; not seen in captures, left alone.
Measured at 1024x768: **table 789 px, container 765 px (24 px over, sideways scroll inside the table region; page itself 1009/1009).** Columns: Channel 97, Transition 143, Status 125, Attempts 76, Sent (UTC) 99, Next attempt (UTC) 97 (header wraps to three lines), Error 153. Error is 153 not 12ch (~100) because `break-word` does not lower min-content: the widest unbreakable token "channel_not_found:" sets it (about 137 px of text). The other columns are at their cell min-content (nowrap times, pills, "ACKNOWLEDGED"). No pill padding or column merging done, per the ruling. 1280: 969/969, 1440: 1129/1129.
Opened: `alert-detail-open-1024x768`, `alert-detail-confirm-1024x768`, `alert-detail-open-1280x800`. 1024: the Error cell reads "HTTP 502 from / receiver" and "channel_not_found: / the Slack channel / no longer exists, / update the / notification target": whole words only, the long token on its own line, no mid-word breaks. The header "Next attempt (UTC)" wraps to three lines. A horizontal scrollbar is present under the table. 1280: every cell on one line (after the nowrap fix), error text in two lines, no scrollbar.
Gates: lint `✔ No ESLint warnings or errors`; typecheck clean; vitest `Test Files  188 passed (188)`, `Tests  2538 passed | 2 todo (2540)`; build `✓ Compiled successfully in 13.4s`. Processes stopped by PID; ports free.

## Round 4: Error column `min-width: 12ch` + `overflow-wrap: anywhere` (FITS at 1024)
Changes: `td.cell-error` is `min-width: 12ch; overflow-wrap: anywhere`. The CSS test pins exactly that pair and rejects `break-all` and `break-word`. Break-it: deleting the `min-width: 12ch` line fails the test; restored. `.delivery-table td.nowrap` kept; `globals.css` untouched (the shared `.nowrap` defect is left for its own task).
Measured at 1024x768: **table 765 px, container 765 px, no scrollbar.** Columns: Channel 97, Transition 143, Status 125, Attempts 76, Sent (UTC) 99, Next attempt (UTC) 98, Error 127. 1280: 969/969; 1440: 1129/1129 (all four alert-detail states with notifications).
Opened `alert-detail-open-1024x768` and `alert-detail-confirm-1024x768`: no sideways scrollbar. Error cells: "HTTP 502 from / receiver" and "channel_not_foun / d: the Slack / channel no / longer exists, / update the / notification target". Every ordinary word is whole; only the over-long token `channel_not_found:` breaks, once. "Next attempt (UTC)" header wraps at its space into two lines.
Gates: lint `✔ No ESLint warnings or errors`; typecheck clean; vitest `Test Files  188 passed (188)`, `Tests  2538 passed | 2 todo (2540)`; build `✓ Compiled successfully in 6.3s`; processes stopped by PID, ports free.
