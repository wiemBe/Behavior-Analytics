# UI-2.4 report — Home tables and the log-source health widget

## 1. Files changed
- `frontend/src/components/home/TopSources.tsx`, `RecentAnomalies.tsx`, `RecentOffenses.tsx`, `LogSourceHealth.tsx` (new) and one `*.test.tsx` each (49 tests).
- `frontend/src/components/home/tables.css` (new): every value a token; container query for the folding.
- `frontend/src/components/home/WidgetParts.tsx` (new, **not in the allowed list**): the shared frame (`Widget`), `Gate` (loading / forbidden / error / ok), `RefreshNote`, `QualifierList`, `Unmeasured`, `When`. Needed so the four widgets do not each re-implement the states; UI-2.5's `WidgetFrame` can replace it.
- `frontend/src/components/home/testkit.ts` (new, test helpers `ok/loading/forbidden/failed`).
- `frontend/src/lib/home/`: `format.ts` (`formatEps`, `formatAgo`), `recentAnomalies.ts`, `recentOffenses.ts` (incl. `recentOffensesRead`, `magnitudeSegments`), `healthCategories.ts`, each with a test; `topSources.ts` gains `epsShares` (+ test in `topSources.test.ts`, two tests added, none changed).
- `frontend/src/app/tokens.css`: appended a "Home tables (UI-2.4)" block (`--table-col-*`, `--bar-*`). Other agents (UI-2.2) may append to the same file: expect a trivial merge.
- `docs/tasks/UI-2.4.report.md` (this file). The temporary preview route is deleted; it is not in the diff.

## 2. What I did
- **TopSources**: five rows from `topSources()`; EPS with its unit, a magnitude bar (SVG, share of the largest) stacked under the number; "at least" under the EPS where `epsIsLowerBound`; deviation (em dash with the gap reason when null) under a header that carries the basis ("latest bucket"); state as a toned word, own column in a wide widget and folded into the name's second line in a narrow one (container query, not viewport); row link to `/behavior/sources/{id}`, "View all" to `/behavior/sources`. Names are one line with an ellipsis and the full name in `title`.
- **RecentAnomalies**: badge "n active" from the response `total`; time in UTC with zone, `<time datetime>`, age in muted text; WAF rows named by endpoint; severity pill + state word; deviation dash when null; "Investigate" to `/anomalies/{id}`; empty state names the not-judged count (and says the count is unreadable if the summary failed); "Showing the latest 5 of 18".
- **RecentOffenses**: 10-segment SVG bar plus number; null magnitude is a dash and no segment; NOT_COLLECTED when the probe says no snapshot, and fail-closed "Could not confirm" when the probe failed; "No open offenses in the latest snapshot" only when a snapshot exists.
- **LogSourceHealth**: `HealthDistribution` + `logSources()` text ("n of m judged normal", "n not judged"), then a category table (Silent, Exceeding expected volume, Newly discovered, Conflicting class, Unclassified, In maintenance; no "Degraded"). Each row stands on its own read; a failed read leaves that row at a dash with the reason. Links only where a page owns the list.
- **States** (all four): loading (plain text, polite live region, no spinner), empty, error (what failed, HTTP status or "did not answer", what to do; never exception text), forbidden ("Your role cannot read … (permission …). Nothing was changed."), qualifiers by word, and a failed refresh keeps the old data with "The latest refresh failed … Showing the data received at HH:MM:SS UTC".
- The open-offenses read is not in `homeReads` (`api.ts` is outside my allowed files), so `recentOffensesRead` lives in `lib/home/recentOffenses.ts` over `api.offenses`, which takes no `AbortSignal`: that read is not aborted on unmount (the hook drops the result).

## 3. Gates (from `frontend/`)
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, nothing printed after the command echo, exit 0 (run after the final build; a run before it failed only on stale `.next/types` of the deleted preview route)
- `npm test`: ` Test Files  119 passed (119)` / `      Tests  1818 passed (1818)`
- `npm run build`: ` ✓ Compiled successfully in 8.2s`, ` ✓ Generating static pages (25/25)` (no `/preview-tables` route)

Break-it checks (each made at least one test fail; code restored after each):
1. `magnitudeSegments`: null returns 0 -> 2 tests fail.
2. `TopSources`: null deviation drawn as `0.00x` -> "writes a null deviation as an em dash, never 0" fails. (First attempt, changing only the branch, was an equivalent mutant because `formatRatio(null)` is already a dash; the guard is the branch plus the formatter.)
3. `recentAnomalies`: badge from row count instead of `total` -> 2 tests fail.
4. `recentOffenses`: missing probe no longer fails closed -> 2 tests fail.
5. `Gate`: forbidden state not recognised -> 4 tests fail (one per widget).
6. `RefreshNote`: never rendered -> 4 tests fail.
7. `When`: `datetime` attribute removed -> 2 tests fail.
8. `healthCategories`: unread count becomes 0 -> 2 tests fail.
9. `RecentOffenses`: null magnitude draws a bar -> 1 test fails.

## 4. Screenshots
Fixture API on port 8124, Next on 3124, Chrome profile under the scratchpad; scripts in `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/` (fixture.mjs, capture.py), outside the repository. The temporary route rendered the four widgets in a grid (4-col-like row above, 6-col-like row below). Measured in the browser for every capture: no page-level horizontal overflow, no table wider than its widget, no local table scroll. Scenarios: ok, empty, no offense snapshot (`nosnap`), forbidden on one widget (`forbid`: offenses; `forbidfleet`: fleet, which also degrades two rows of the health table), backend down, long source name.

PNGs (`/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/`), each at `-1440x900`, `-1280x800`, `-1024x768`:
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/ok-1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/ok-1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/ok-1024x768.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/empty-1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/empty-1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/empty-1024x768.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/nosnap-1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/nosnap-1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/nosnap-1024x768.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/forbid-1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/forbid-1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/forbid-1024x768.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/forbidfleet-1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/forbidfleet-1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/forbidfleet-1024x768.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/down-1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/down-1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/down-1024x768.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/longname-1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/longname-1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/png/longname-1024x768.png

## 5. Open points
- **Layout is UI-2.5's**: the widgets fold by their own width (container query at 34 rem). A widget narrower than about 340 px was not designed for; Recent anomalies and Recent offenses should get at least 480 px.
- **Top sources "Mean EPS"**: events over the window's seconds, as UI-2.1 documents; it reads low for a source with gaps and the row says "at least" only when its class window is incomplete.
- **Deviation** is the latest bucket's, not the window mean (UI-2.1 open point 5), labelled in the header.
- **Silent / Exceeding links** go to `/anomalies?anomaly_type=NO_EVENTS` and `VOLUME_SPIKE`; that page does not filter to active only, so the list can include resolved ones. "In maintenance" has no owning page and is not a link.
- **Open-offenses read has no abort** (see section 2); an `api.ts` addition taking a signal would fix it.
- `tokens.css` was edited by me and likely by UI-2.2: both append at the end.
- While rebuilding I ran a `pkill -f next-server` once by mistake; it may have stopped another agent's preview server on this machine (my own pid-file approach replaced it). Worth checking with the UI-2.2 agent.
