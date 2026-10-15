# UI-5.4 report: the fleet pages reviewed and fixed

## 1. Files changed
- `frontend/src/app/fleet/catchall/catchall.css` (new): the 122 lines of catch-all rules moved out of `globals.css`, re-read against the new tokens.
- `frontend/src/app/fleet/catchall/page.tsx`: imports `./catchall.css`.
- `frontend/src/app/fleet/catchall/BucketChart.tsx`: y-axis name `Events (events / 5 min)` becomes `Volume (events / 5 min)`.
- `frontend/src/app/globals.css`: only the catch-all block removed (every rule is in `catchall.css`; checked rule by rule).
- `frontend/src/app/tokens.css`: five new tokens (below).
- `docs/tasks/UI-5.4.report.md`: this file.

New tokens (each replaces a literal that was in `globals.css`):
- (round 1 values, superseded by section 7) the sender and source column bounds and `--catchall-seen-label-w: 5ch` (the sender and source column bounds and the "First/Last" label width).
- `--catchall-bucket-col-min: 15rem` (was `240px`, same value at a 16px root).
- The `10px` chart-key bars now use the existing `--bar-h` (8px). Visible change: the key swatches are 2px thinner.

## 2. Review of the inherited work
- The "before" PNGs (09:54 to 09:57) predate every edit (09:59:13), so they show the untouched code. Kept. Three extra `fleet-catchall-*` files in `png/before/` are from an earlier capture; they are not counted in the 24.
- The fixture serves the backend's payload shapes (`fleet`, `classification`, `catchall` schemas). Used as is; it holds only one conflicting source, so the conflict page is thin.
- The inherited CSS move broke the page (regression found in my first "after" capture): `table.catchall-table { min-width: 60rem }` also matched the bucket's top-senders table, and the document scrolled sideways (scrollWidth 1987 over 1425 at 1440, 1197 over 1009 at 1024). I removed that rule. A 60rem minimum on the queue alone also pushed Review and Actions out of view at 1024, so no table minimum remains. The `--catchall-table-min` token was dropped.
- The inherited `--catchall-sender-min: 18ch` made the queue scroll by 23 px at 1024. It is now 15ch and the queue fits (765 of 765).
- The selected-range button (`.catchall-volume button[aria-pressed="true"]`) used the cyan info colour and a bold weight. It now uses the neutral selection of Home and the other range controls (`--surface-3`, `--border-strong`, `--text`). Selection is UI state, not status.

## 3. Defect list
| # | Route / viewport | What is wrong | Rule | Fix |
|---|---|---|---|---|
| 1 | /fleet/catchall, all | 122 lines of navy-era CSS in `globals.css` with literals (`30ch`, `240px`, `10px`, info-coloured selection) | §3a tokens; UR.1.2 | Moved to `catchall.css`, literals to tokens, selection neutral |
| 2 | /fleet/catchall, all | Range button selected in cyan and bold, unlike Home | consistency (task note) | Neutral selection (fixed) |
| 3 | /fleet/catchall chart | Axis name `Events (events / 5 min)` says "events" twice | §3a unit on axis | `Volume (events / 5 min)` |
| 4 | /fleet/catchall chart, key | `10px` literal bar height | §3a no literals | `--bar-h` |
| 5 | /fleet/catchall, 1024 | Sender hostname wraps to six lines in a 7-column table | §9 | Not fixed: the width is bound by the other nowrap columns, not by the sender floor. Open point. |
| 6 | /fleet, 1024 | The `WindowSwitch` drops under the description (the 76ch description plus the controls exceed the content width) | UR.3.3 | Not fixed: `PageHeader` and `.page-header` are shared. Open point. |
| 7 | /fleet/sources, all | "First seen" shows `07-30 18:00 UTC` with no year (`formatDateTimeCompact`, shared in `lib`) | times carry a timezone, unambiguous | Not fixed: outside the allowed files. Open point. |
| 8 | /fleet/catchall chart | Not-measured shading (`--chart-gap`, a warn tint) is the same hue as the LOWER_BOUND fill and paints most of a sparse chart | UR.2.6 | Not fixed: the token is shared with other charts. Open point. |
| 9 | /fleet, /fleet/sources | Overflow, tables inside `.table-scroll`, units, right-aligned numbers, empty and filter states: checked, no defect | §9 | none |

Measured: no page overflow on any of the 24 captures, after (table inside its scroll container 765 of 765 at 1024).

## 4. PNGs
Before: `/home/efe/Documents/QRadar-Dash-work/ui54/png/before/`, after: `/home/efe/Documents/QRadar-Dash-work/ui54/png/after/`. Both hold `{fleet-1h, fleet-24h, fleet-7d, fleet-sources-filtered, fleet-sources-empty, fleet-sources-conflict, fleet-catchall-queue, fleet-catchall-volume}-{1440x900,1280x800,1024x768}.png`.
Per route: /fleet and /fleet/sources render identically before and after (no change made). /fleet/catchall: range buttons neutral, axis name "Volume (...)", key bars 8px; no layout change.

## 5. Gates (from `frontend/`)
- `npm run lint`: `No ESLint warnings or errors`
- `npm run typecheck`: no output (clean)
- `npx vitest run --no-cache`: `Test Files  162 passed (162)` / `Tests  2326 passed (2326)`
- `npm run build`: `Compiled successfully`
No test was edited and no markup was added, so no new test. Processes (Next 201953, Chrome 192919, fixture 186602) were stopped by PID.

## 6. Open points
Defects 5 to 8 need `PageHeader`, `lib/health`, `chartPalette`/`tokens.css` chart tokens, or `FleetClassTable`/`FleetSourcesTable` changes beyond this task's allowed files.

## 7. Round 2 (orchestrator review)
Token set after round 2 (all in `tokens.css`): `--catchall-sender-min: 32ch`, `--catchall-source-min: 17ch`, `--catchall-source-max: 17ch`, `--catchall-source-narrow: 12ch`, `--catchall-note-narrow: 14ch`, `--catchall-seen-label-w: 5ch`, `--catchall-bucket-col-min: 15rem`, `--catchall-top-max: 44rem`. Only `catchall.css` and `tokens.css` changed.

**1. Hostname.** Changes, all in `catchall.css`:
- The sender column takes the width the others leave (`th.cell-sender { width: 100% }`). The hostname block has a 32ch floor in the monospace face (`.sender-id`), with no ellipsis, so the full name stays readable and copyable.
- At 1280 and below the First/Last labels sit above their times, the review note wraps at 14ch, the unit drops under the event count, header cells may wrap, and Review and Dismiss stack (one column is narrower than two).
- At 1100 and below the source column narrows to 12ch.
- `/fleet/catchall` columns kept at their natural widths. Nothing scrolls inside its container.
- The "Top senders in this bucket" table now takes a full row of the detail grid (capped at 44rem), so hostnames stay on one line at all three widths instead of breaking at a dot.

Column widths in px (header cell widths), 50-character test name injected into one row:

| viewport | state | Sender | Source | Seen | 24 h events | Review | Actions | hostname lines | table scroll |
|---|---|---|---|---|---|---|---|---|---|
| 1440 | before (round 1) | 282 | 205 | 154 | 115 | 195 | 177 | 57 ch: 2, 36 ch: 1 | none (1129/1129) |
| 1440 | after | 482 | 139 | 148 | 110 | 117 | 131 | 50 ch: 1, 57 ch: 1 | none |
| 1280 | before | 223 | 169 | 148 | 110 | 162 | 156 | 57 ch: 3 | none (969/969) |
| 1280 | after | 450 | 139 | 107 | 80 | 97 | 95 | 50 ch: 1, 57 ch: 2 | none |
| 1024 | before | 137 | 120 | 148 | 110 | 118 | 132 | 57 ch: 7, 36 ch: 4 | none (765/765) |
| 1024 | after | 287 | 98 | 107 | 80 | 97 | 95 | 50 ch: 2, 57 ch: 2 | none (765/765) |

Cost: rows are taller at 1280 and 1024 (about 108 and 115 px, from 79 to 92 px before). The source name wraps to three lines and the Seen pair is four lines.
No page overflow (document scrollWidth equals clientWidth on all captures).

**2. Range control.** The volume range buttons now match Home's `RangeControl` presets (`.wr-preset` in `widgets/dashboard.css`): transparent text buttons in `--text-3`, hover on `--surface-2`, selected on `--surface-3` with `--border-strong`, `--fs-xs`, `--r-sm`. I matched the look in `catchall.css` and did not import the component. It is a widget-only component with a different API, and `SourceVolume.test.tsx` pins the button names ("6 hours"), so the wording stays "6 hours / 24 hours / 7 days". Keyboard behaviour is identical: native buttons with `aria-pressed`, and the global focus ring.

**PNGs (round 2).** Re-captured into `png/after/`: `fleet-catchall-queue-{1440x900,1280x800,1024x768}.png` and `fleet-catchall-volume-{1440x900,1280x800,1024x768}.png`. I looked at all six. The volume capture now scrolls 80 px clear of the top bar so the range control is visible. Extra: `fleet-catchall-bucket-{1440x900,1280x800,1024x768}.png` (bucket detail with the top-senders table). `fleet-catchall-bucket-1280x800.png` was not opened; its widths are in `logs/after-bucket-measurements.json`.
Side effect to note: at 1440 the bucket detail grid leaves its third column empty above the top-senders row.

**Gates (round 2).** `npm run lint`: `No ESLint warnings or errors`. `npm run typecheck`: clean. `npx vitest run --no-cache`: `Test Files  162 passed (162)`, `Tests  2326 passed (2326)`. `npm run build`: `Compiled successfully`. No test edited, no markup added.
