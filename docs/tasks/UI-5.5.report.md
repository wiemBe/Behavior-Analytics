# UI-5.5 report: offenses, rules and coverage reviewed and fixed

## 1. Files changed
- `frontend/src/app/offenses/page.tsx`, `offenses.css` (new): `PageHeader` (h1), table in `TableScroll`, dense, `num` on Sev/Events/Age, thousands separators on Events, magnitude pill with a bar beside it (steps by class, no inline width), error notice `role="alert"` with a recovery line.
- `frontend/src/app/offenses/[id]/page.tsx`, `offense-detail.css` (new): unmeasured counts are "—" (T-096 defects), magnitude bar under the number, tables in `TableScroll`, history figures `num`, `PageHeader` on the 404/error/bad-id states, roles on notices, recovery line and back link on a bad id. The title of the normal view stays an `h2` (see open point 1).
- `frontend/src/app/rules/page.tsx`, `rules.css` (new): `TableScroll`, `num` on Offenses, compact UTC time on one line, alert notice with recovery line, column floors.
- `frontend/src/app/rules/[id]/page.tsx`: `PageHeader` (h1) everywhere, a failed health-history read is no longer shown as "no evaluation stored" (own alert), tables in `TableScroll`, history figures `num`, times nowrap, roles on notices.
- `frontend/src/app/coverage/page.tsx`, `coverage.css` (new): `PageHeader`, three tables in `TableScroll`, `num` on Rules/Score, a status present in `by_status` but without a fixed card gets a card (cards now add up), alert notice with recovery line.
- `frontend/src/components/rules/SilentRules.tsx`: "Not judged, by type" table in `TableScroll`; `lowerFirst` no longer turns "FLOW rules" into "fLOW rules".
- Tests: `offenses/page.test.tsx`, `offenses/[id]/page.test.tsx`, `rules/page.test.tsx`, `rules/[id]/page.test.tsx`, `coverage/page.test.tsx`, `rules/statistics/page.test.tsx` (new tests; the two T-096 `it.todo` became real tests; no other existing test edited).
- No new token. `tokens.css`, `globals.css`, `components/ui/*` untouched.

## 2. Defect list
| # | Route / viewport | What is wrong | Rule | Fix |
|---|---|---|---|---|
| 1 | /offenses/[id], all | Events/Flows drawn `0 / 0` when unmeasured (T-096) | no zero for a missing measurement | "— / —"; a measured 0 stays 0 |
| 2 | /offenses/[id], all | Sources/Destinations drawn `0 / 0` (T-096) | same | same |
| 3 | /offenses, /rules, /coverage, /offenses/[id], /rules/[id], all | Page titles were bare `h2` + subtitle, no `h1`, title lower than on `PageHeader` pages | UR.3.3, consistency | `PageHeader` (detail title of the two offense/rule success states: open point 1 for offenses; /rules/[id] now an h1) |
| 4 | /offenses | Magnitude was a coloured pill only | UR.1.2 bar plus number | number pill plus bar; none when null |
| 5 | /offenses, /rules, /coverage, details, 1024 | Tables not in `TableScroll` | guidelines §9 | `TableScroll` on every table (also the "Not judged" table on /rules/statistics, a raw table before) |
| 6 | /offenses, /rules, /coverage, details | Figures (Sev, Events, Age, Offenses, Rules, Score, history) left-aligned | numbers right-aligned | `num` on header and cell |
| 7 | /rules, 1024 | Last-fired time broke into 3 lines ("2026-10- / 14 / 17:00:00 / UTC"); rule name in a 5-line column | times never wrap | compact UTC time, nowrap, name/meaning floors |
| 8 | /offenses, 1024 | Started wrapped to two lines | same | nowrap, dense table |
| 9 | /rules/[id] | A failed health-history read said "No rule-health evaluation has been stored yet" | fail closed, honest empty vs failed | own `role="alert"` ("could not be loaded ... does not mean no evaluation was stored") |
| 10 | /rules/statistics | "because fLOW rules are not searched" | text defect | initialisms kept |
| 11 | /coverage | A status in `by_status` with no fixed card (e.g. INSUFFICIENT_DATA) was counted nowhere, cards did not add up to "N techniques" | honesty | extra card per unlisted status |
| 12 | error states of all routes | Terse "Backend unreachable ..." | error says what failed and what to do | same first sentence (tests pin it) plus "Nothing was changed; reload once the backend answers", `role="alert"` |
| 13 | /offenses/[id], /rules/[id] | 404 "not collected" and failure had no role | a11y | `status` for not collected, `alert` for failure |
| 14 | /offenses/[id] bad id | Bare one-line notice, no way back | error state | recovery line and back link |
| 15 | /offenses/[id], /rules/[id] | Key/value tables: key column width jumped with content | consistency | fixed 22ch key column |

Measured: no page overflow on any of the 87 captures (document scrollWidth equals clientWidth); no raw table outside a scroll region after. No inline style in any route (tests assert it). No pie/donut/chart exists on these routes. Filters (`OffenseFilters.tsx`, rules filter form): already labelled (`aria-label`), native controls, keyboard usable, same `.filters` class as the other list pages: unchanged.

## 3. What changed per route (PNGs looked at)
- /offenses: header at the shared position, bar beside the magnitude, right-aligned figures with separators, time on one line. At 1024 the table is wider than its column (817 of 765 px; 790 filtered) and scrolls inside its own region; Age is reached by scrolling.
- /offenses/[id]: "— / —" for unmeasured counts, bar on the magnitude card, same title scale as the page-title pages.
- /rules: compact time, readable name column, figures right-aligned; at 1024 the table scrolls inside itself (853 of 765).
- /rules/[id]: h1 header, history failure no longer reads as empty; the capacity failure was already honest.
- /rules/statistics: already on the new system, only the "fLOW" text and the one raw table changed.
- /coverage: header, scroll regions, right-aligned figures, cards that add up; fits at 1024 (765 of 765).
PNGs (87 each, `{route}-{1440x900,1280x800,1024x768}.png`):
- Before: `/home/efe/Documents/QRadar-Dash-work/ui55/png/before/`; after: `/home/efe/Documents/QRadar-Dash-work/ui55/png/after/`.
- Routes: `offenses`, `offenses-filtered`, `offenses-nomatch`, `offenses-empty`, `offenses-error`, `offense-detail`, `offense-detail-nulls`, `offense-detail-histfail`, `offense-detail-404`, `offense-detail-error`, `offense-detail-badid`, `rules`, `rules-filtered`, `rules-nomatch`, `rules-empty`, `rules-error`, `rule-detail`, `rule-detail-nowindows`, `rule-detail-failed`, `rule-detail-insufficient`, `rule-detail-404`, `rule-stats`, `rule-stats-partial`, `rule-stats-nulls`, `rule-stats-noinst`, `rule-stats-error`, `coverage`, `coverage-none`, `coverage-error`.
- Measurements: `.../ui55/logs/{before,after}-measurements*.json`. Fixture with backend payload shapes: `.../ui55/fixture/`.
- Looked at by eye: before: offenses 1280, offense-detail-nulls 1024, rules 1024, coverage 1024, rule-detail-failed 1024, rule-stats-nulls 1024, rule-stats-partial 1024. After: offenses 1024 (two builds), offense-detail-nulls 1024, offense-detail 1440, offenses-error 1024, rules 1024 (first build), rule-detail-failed 1024, coverage 1024. The final-build recapture (width floors, narrower bar) was checked by table-width measurements only; the others (all 1280 and most 1440 captures, empty/404/error states) by measurements only, not by eye.

## 4. Tests
- 2 T-096 `it.todo` converted (fail on the old `?? 0`, pass now); todo count 0.
- About 22 new tests: h1, scroll regions, `num` header and cell, bar present and absent, no inline style, alert/status roles, failed vs empty health history, extra coverage status card, initialism after "because".
- Break-it: 15 single-line mutations (script `.../ui55/breakit.py`), all failed their test; sources restored after each.

## 5. Gates (from `frontend/`)
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, no output
- `npx vitest run --no-cache`: `Test Files  188 passed (188)` / `Tests  2545 passed (2545)` (an earlier run under machine load had one unrelated 5 s timeout in `IpsProfileEditor.test.tsx`; the clean rerun passed)
- `npm run build`: `✓ Compiled successfully in 37.3s`
Processes I started (Next, Chrome, fixture) were stopped by PID.

## 6. Open points
1. **`/offenses/[id]` title is still an `h2`.** Its tests pin `level: 2` (three assertions). I styled it as a page title locally (`.offense-title`). Fix: change those assertions to level 1 and use `PageHeader`.
2. **A failed offense-history read still reads "No history yet. A second snapshot is written only when the offense changes..."**: `page.test.tsx` pins `/No history yet\./` for the failed read, so I could not give it its own honest wording. Same fix pattern as /rules/[id] once the test may change.
3. At 1024 the offense list (817 px) and rule list (853 px) scroll sideways inside their own region; nothing is hidden from keyboard users, but Age / Last fired need a scroll. Dropping or folding a column (for example the Meaning column) would fix it and changes the pinned columns.
4. The offenses list time is full `YYYY-MM-DD HH:MM:SS UTC` because a test pins that string.
5. `/coverage` shows no Confidence column although the payload carries `confidence` (adding one would change pinned em-dash counts). A technique with `coverage_score` 0 shows "0%": that is the backend's value, not a missing one.
6. `MostMatched` renders a lower-bound tiny rate as "≥ < 0.01"; contradictory but honest. Not changed.
7. Rule `StatCard` row on /rules and the six-card row on /offenses are still rows of equal cards (they hold counts that matter; layout pinned by the tests).

## Round 2

### Files changed
- `app/offenses/[id]/page.tsx`, `offense-detail.css`: `PageHeader` (h1), CSS imitation removed; a failed history read now shows its own `role="alert"` ("The history could not be loaded. ... Reload the page"), an empty successful read keeps "No history yet."; Events/Flows, Sources/Destinations and history Events use thousands separators; metric group class `offense-cards offense-cards-3`.
- `app/offenses/page.tsx`, `offenses.css`: header "Started (UTC)", cell `axisTimeLabel` (no zone), dense table, Description floor 20ch, metric group `offenses-cards-3`.
- `app/rules/page.tsx`, `rules.css`: header "Last fired (UTC)" (may wrap to two lines), cell `axisTimeLabel`, dense table, Rule floor 20ch, Meaning floor 9ch, metric group sized from its count (`rules-cards-N`, N = ceil(count/2)).
- `app/rules/[id]/page.tsx`: dense health-history table, Reason floor 20ch (history table was 840 of 765, now 765), metric group `rule-cards-2`.
- `app/coverage/page.tsx`, `coverage.css`: metric group `coverage-cards-N`, evidence floor 14ch.
- Card groups: one row at 1440 and 1280 (`repeat(auto-fit, minmax(0, 1fr))`), at most two balanced rows at <=1100 px by class (6 -> 3+3, 7 -> 4+3, 4 -> 2+2, 5 -> 3+2), compact padding. Card count, order and text unchanged.

### Test edits (every one)
Authorised:
1. `offenses/[id]/page.test.tsx`: two `level: 2` -> `level: 1`.
2. same file: "keeps the offense when only the history read fails" no longer asserts `/No history yet\./`; it asserts the alert text and that "No history yet." is absent. New test: a successful empty read still says "No history yet." with no alert.
3. `offenses/page.test.tsx`: the Started assertion `2026-10-14 22:10:00 UTC` -> `10-14 22:10`, and the cell must not contain "UTC".
Consequences of the ordered changes, not in your list, please confirm:
4. `offenses/page.test.tsx`: the header name `"Started"` -> `"Started (UTC)"` (the ruling's own header change).
5. `offenses/[id]/page.test.tsx`: `"1280 / 12"` -> `"1,280 / 12"` and history `"1280"` -> `"1,280"` (item 4, thousands separators).
(My own Round-1 rules test for the last-fired cell was updated to the new header and compact time.)

### New tests (about 8)
Separators in the detail counts, 3+3 / 4 / 2+2 group classes on offenses list, offense detail, rules list (7 cards -> `rules-cards-4`), rule detail, coverage; "Last fired (UTC)" header with a zone-free cell; alert on failed history; empty-history case.

### Column widths at 1024x768 (table / container, px)
| Route | Before | After |
|---|---|---|
| /offenses | 817 / 765 | 765 / 765 (ID 50, Description 185, Mag 79, Sev 37, Status 82, Assigned 92, Events 74, Started (UTC) 97, Age 68) |
| /offenses filtered | 790 | 765 / 765 |
| /rules | 853 / 765 | 765 / 765 (Rule 177, Type 83, Enabled 83, Health 151, Meaning 101, Last fired (UTC) 104, Offenses 68) |
| /rules/[id] health history | 840 | 765 / 765 |
| /coverage tables | 765 | 765 / 765 |
No page overflow on any of the 72 re-captured states (document scrollWidth equals clientWidth, no raw table). Floors are 20ch not 22ch: at 22ch the two lists did not fit (offenses 774, rules 789 with the other floors). A Description or Rule title reads in 2 to 4 lines at 1024; Meaning reads in up to 5 lines for the longest sentence.

### PNGs
Re-shot into `/home/efe/Documents/QRadar-Dash-work/ui55/png/after/`: offenses, offense-detail, rules, rule-detail, coverage families at all three viewports (the last pass: the five list/detail routes only; states re-shot in the previous pass). Opened by eye: offenses-1024, rules-1024, offense-detail-1440, offense-detail-1024, offense-detail-histfail-1024, rules-1280, coverage-1280, coverage-1024, rules-1280 again after the last CSS gap. Saw: offenses fits with Started and Age visible, cards 3+3; rules at 1024 cards 4+3 and the table fits with Last fired and Offenses visible; offense-detail 1440 six cards in one row, "54,760 / 0", history "1,480"; offense-detail 1024 cards 3+3; histfail shows the "The history could not be loaded." alert; rules and coverage at 1280 in one card row (rules 7, coverage 6), coverage 1024 cards 3+3.

### Gates
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, no output
- `npx vitest run --no-cache`: `Test Files  188 passed (188)` / `Tests  2552 passed (2552)` (no timeout this run)
- `npm run build`: `✓ Compiled successfully in 4.5s`
- Break-it: 12 mutations of the new/changed behaviour (`.../ui55/breakit2.py`), all failed their tests, sources restored.
- Next, Chrome and the fixture were stopped by PID.

### Open points
- Meaning text on /rules wraps to 4 to 5 lines at 1024 for long sentences; a shorter sentence or hiding the column is a product call.
- The offenses 1024 table is exactly 765 of 765, no slack: a longer Assigned name or a 5-digit Events value would make it scroll inside its region again.
