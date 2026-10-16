# UI-5.9 report

Reviewed the inherited tree against `docs/tasks/UI-5.9.md`, kept the changes that match the spec, and corrected points 1, 5 and 10. No commit, push, branch switch, stash or reset. `backend/` was not touched. `shellFixes.test.ts`, `EvidenceBanner` and `formatRate` were not edited.

## Files changed

- `frontend/src/components/waf/wafLabels.ts` — `statePillText` returns the backend state in upper case, underscore kept.
- `frontend/src/components/waf/WafIncidentHeader.tsx` — the identity pill uses `statePillText`.
- `frontend/src/components/waf/WafEndpointsTable.tsx` — the endpoint state pill uses `statePillText`.
- `frontend/src/components/waf/WafInvestigation.tsx` — the lifecycle list uses `statePillText`. Chart transition labels still use `stateLabel`.
- `frontend/src/components/ips/IpsIncidentHeader.tsx` — the identity pill uses `statePillText`.
- `frontend/src/components/ips/IpsInvestigation.tsx` — the lifecycle list uses `statePillText`. Chart transition labels still use `stateLabel`.
- `frontend/src/components/catalog/RecentIpsAnomalies.tsx` — the list pills use `statePillText`.
- `frontend/src/components/waf/wafLabels.test.ts` — new `statePillText` cases.
- `frontend/src/components/waf/WafInvestigation.test.tsx` — pill and lifecycle assertions expect the backend spelling.
- `frontend/src/components/waf/WafEndpointsTable.test.tsx` — pill assertions expect `NORMAL` and `INSUFFICIENT_DATA`.
- `frontend/src/components/ips/IpsInvestigation.test.tsx` — new upper-case identity-pill assertion.
- `frontend/src/components/catalog/ipsAnomalies.test.tsx` — new upper-case list-pill assertion.
- `frontend/src/app/behavior/waf/sources/[id]/page.layout.test.tsx` — one assertion now expects `INSUFFICIENT_DATA`. This file is outside the allowed lists. See below.
- `frontend/src/components/behavior/TopContributors.tsx` — FAILED empty state refers to the banner in a few words.
- `frontend/src/components/behavior/DimensionExplorer.tsx` — empty Evidence tab, when collection failed, refers to the banner.
- `frontend/src/app/anomalies/[id]/page.tsx` — passes `evidenceStatus` into `DimensionExplorer`.
- `frontend/src/components/behavior/TopContributors.test.tsx` — new file. Pins the short FAILED sentence.
- `frontend/src/components/behavior/DimensionExplorer.test.tsx` — new test for the short Evidence-tab sentence.
- `frontend/src/app/globals.css` — scroll-driven rule on `.sticky-actions`; `grid-auto-rows: auto` on `.grid-4` at `max-width: 1080px`; sidebar rhythm tightened at `max-height: 820px` with existing spacing tokens.
- `frontend/src/app/stickyAndStatGrid.test.ts` — new file. Pins the sticky rule and the 2×2 row sizing.
- `frontend/src/app/shortViewportNav.test.ts` — new file. Pins the short-viewport nav tokens.
- `frontend/src/lib/behavior.ts` — `stateMeaningAgainst` keeps the band sentence only when the observed value is inside the band.
- `frontend/src/app/behavior/sources/[id]/page.tsx` — the source subtitle passes observed EPS and the expected band. Outside the point-5 file list. Required so the sentence sits beside those numbers.
- `frontend/src/lib/behavior.test.ts` — new cases for inside, outside, edges and an unknown band.
- `frontend/src/components/rules/MostMatched.tsx` — a sub-floor lower bound renders `< 0.01 (lower bound)`.
- `frontend/src/components/rules/MostMatched.test.tsx` — new file.
- `frontend/src/components/shell/TopSearch.tsx` — placeholder shortened to `Search sources, rules, anomalies`. The aria-label still names offenses.
- `frontend/src/components/behavior/AnomalyFilters.tsx` — instance placeholder shortened to `Instance…`.
- `frontend/src/components/shell/placeholders.test.tsx` — new file.
- `frontend/src/components/catalog/HealthDetection.tsx` — verdict words stay upper case, with underscores read as spaces, matching the coverage page.
- `frontend/src/components/catalog/catalog.test.tsx` — verdict assertions updated, plus one new case.
- `frontend/src/components/home/HomeDashboard.tsx` — `Template, not saved` is a `badge`; the description is the following `dg-qualifier`.
- `frontend/src/widgets/dashboard.css` — that description takes a full flex line.
- `frontend/src/components/home/HomeDashboard.test.tsx` — new assertion that the template label is a badge.
- `docs/tasks/UI-5.9.report.md` — this report.

## What you did

The anomaly list already prints the backend state string (`OPEN`, `INSUFFICIENT_DATA`). There is no separate formatter on that list. `statePillText` is `state.toUpperCase()`, so a WAF or IPS pill shows the same string, underscore included. Sonnet had turned the underscore into a space (`INSUFFICIENT DATA`), which is a different spelling from the list. Chart labels in the same investigations still go through `stateLabel` (title case). They are not the pill.

On a FAILED incident the full sentence stays in `EvidenceBanner`. What changed says `No contributors: evidence collection failed (see the notice below).` The Evidence tab says `Evidence collection failed; see the notice above.` Those two sentences were not pinned by any existing test, so no existing assertion was rewritten for point 2. New tests pin them. The notices live in `TopContributors.tsx` and `DimensionExplorer.tsx`, which the point-2 file list does not name. The page file that wires `evidenceStatus` is in the list.

`.sticky-actions` no longer has a static inset shadow. Under `@supports (animation-timeline: scroll())` a `scroll(nearest inline)` animation draws `inset 1px 0 0 var(--border)` from 0% to 96% and a transparent shadow at 100%. A table that is not overflowing has an inactive timeline, so the computed shadow is `none`. At 1440, 1280 and 1024 the anomalies table does not overflow (`scrollWidth` equals `clientWidth`: 1144, 984 and 780) and the shadow is `none`. Before, the same cells computed `rgb(53, 54, 58) 1px 0px 0px 0px inset` while not scrolling. The scrolling case is an extra 880×768 pair, below the spec viewports: at scroll start the inset rule is present (`753/636` at scroll 0); at the end it is transparent (`rgba(0, 0, 0, 0) 0px 0px 0px 0px inset`, scroll 117).

The base `.grid-4 { grid-auto-rows: 1fr }` rule is unchanged, so `shellFixes.test.ts` still matches it and was not edited. At `max-width: 1080px` the 2-column fold sets `grid-auto-rows: auto`. Cards in one row still stretch to each other. A short row is not stretched to a taller row. On the source fixture every card has a note, so all four measured 117px before and after at 1024. The CSS change is what the spec asks for. This fixture does not show a short card beside a noted one.

`stateMeaning("NORMAL")` with no band still says the value is within the expected band, which is what `statusSemantics.test.ts` maps over. `stateMeaningAgainst` says that only when observed, low and high are all finite and observed is inside the band, edges included. Outside, the sentence is `NORMAL by the detector's verdict for this weekday and hour; the observed value shown here is not inside the expected band.` A missing number yields `NORMAL by the detector's verdict for this weekday and hour.` and does not mention the band. Sonnet's version treated a missing bound as outside and said `not inside`.

Point 6 uses `< 0.01 (lower bound)`. `≥ 0.01` would be false. Extra digits would invent precision below the display floor. `formatRate` is unchanged. A real lower bound of 1.5 still renders `≥ 1.50`.

At 1024 the only clipped fields in the captures were the top search (`Search sources, rules, offenses, anomalies`, text width 263 against a field about 242–249) and, with More filters open, `QRadar instance…` (113 against 86). The page fields on sources, rules, offenses, catch-all and log sources were not in the clipped set. After the two shortenings, the clip list is empty at 1440, 1280 and 1024. At 880 the shortened top search still clips. That width is outside the spec.

The sidebar is sticky, `height: calc(100vh - var(--topbar-h))`, and it scrolled. Before: scroll height 754 against client 712 at 1024×768, and 754 against 744 at 1280×800. At 1440 it already fitted (844/844). `@media (max-height: 820px)` tightens sidebar padding and group margin and padding to existing `--sp-*` tokens. `0` on the label's margin is not a new length. `.nav-link` is untouched, so the text-only entries stay. After: 712/712 at 1024×768, 744/744 at 1280×800, 844/844 at 1440×900. IPS sources is visible at the bottom of the 1024 sidebar.

`HealthDetection` words verdicts with underscores replaced by spaces, so they stay upper case (`HEALTHY`, `DEGRADED`, `SILENT`, `DEPENDENCY DEGRADED`), which is how the coverage page writes them. The other catalog widgets were checked. `HealthEvidence` is consistent title case (Pending, Failed). WAF source validation labels are already `VALID` / `INVALID`. Fleet classes, outage policy and health collection do not mix case inside one status. They were left as they are.

`Template, not saved` is a `badge`. The description is the next `dg-qualifier`, with `flex-basis: 100%` so it is its own line. `100%` is a layout fraction, not a new colour, spacing or type literal. Measured badge top and description top: 129 and 161 at 1440 and 1280, 121 and 153 at 1024.

### Existing test edits (`git diff -- '*.test.*'`)

No test was skipped, xfailed or deleted. `shellFixes.test.ts` has no diff.

New tests only, existing assertions unchanged:

- `DimensionExplorer.test.tsx` — added the FAILED referral test.
- `ipsAnomalies.test.tsx` — added the upper-case pill test.
- `HomeDashboard.test.tsx` — added the badge test. The existing template test is unchanged.
- `IpsInvestigation.test.tsx` — added the `INSUFFICIENT_DATA` pill test.
- `wafLabels.test.ts` — added the `statePillText` describe. Existing `stateLabel` tests are unchanged.
- `behavior.test.ts` — added the band describe.

Existing assertions changed, inside allowed files, because the pill now matches the backend spelling:

- `WafEndpointsTable.test.tsx` — `Normal` to `NORMAL`; `Insufficient data` to `INSUFFICIENT_DATA`.
- `WafInvestigation.test.tsx` — `Open` to `OPEN`; `Insufficient data` to `INSUFFICIENT_DATA`; `Candidate` to `CANDIDATE`. The lifecycle test was retitled from `words both ends of every lifecycle transition, never as an enum with an underscore` to `shows both ends of a lifecycle transition as the backend names them`. It now expects `INSUFFICIENT_DATA` and `NORMAL`, and the negative queries for those two strings were inverted to the title-case forms.
- `catalog.test.tsx` — `Never observed` to `NEVER OBSERVED`; the note from `Missing 100` / `Not evaluated 20` to `MISSING 100` / `NOT EVALUATED 20`. A new test expects `HEALTHY`, `DEGRADED`, `SILENT` and `DEPENDENCY DEGRADED`.

Outside the allowed lists, one assertion:

- `app/behavior/waf/sources/[id]/page.layout.test.tsx` — `Insufficient data` to `INSUFFICIENT_DATA` in `shows a long path in full, and a never-evaluated endpoint as not evaluated`. The page renders `WafEndpointsTable`, so the suite cannot pass with the old string. `Not evaluated` and the absent `Normal` query are unchanged.

### Break-it

Each mutation was restored. A following grep confirmed the production string was back. Every targeted test failed:

1. `wafLabels.test.ts` — `shows the backend's state name in upper case, underscore included` (1 failed, 8 passed). Returned the raw state.
2. `TopContributors.test.tsx` — `refers a failed collection to the banner instead of restating it` (1 failed, 1 passed). Restored the long sentence.
3. `stickyAndStatGrid.test.ts` — `has no static rule on the pinned cells` (1 failed, 2 passed). Put the static box-shadow back.
4. `stickyAndStatGrid.test.ts` — `lets rows size to their own cards at the 2x2 fold` (1 failed, 2 passed). Set the fold back to `1fr`.
5. `behavior.test.ts` — `names the detector's verdict, not the band, when the value lies outside it` (1 failed, 34 passed). The outside branch returned the within-band sentence.
6. `MostMatched.test.tsx` — `never reads '≥ < 0.01' for a lower bound, and never claims '≥ 0.01'` (1 failed, 2 passed). Routed the sub-floor lower bound back through `≥`.
7. `placeholders.test.tsx` — `the top search names its scope in a short form` (1 failed, 1 passed). Restored the long placeholder.
8. `shortViewportNav.test.ts` — both tests failed (2 failed). The media query was moved from 820px to 900px, so the pattern matched nothing.
9. `catalog.test.tsx` — `words every verdict in one case, as the coverage page does` (1 failed, 30 passed). Rendered `Healthy`.
10. `HomeDashboard.test.tsx` — `sets 'Template, not saved' apart from its description as a badge` (1 failed, 43 passed). Both spans were `dg-qualifier`.

## Gates

Run from `frontend/` with `PATH` including `~/.local/node/bin`, on the fixed tree, before the screenshot servers. `npm ci` was not run. The task prompt named these four gates and said `node_modules` is installed.

Lint, exit 0:

```
> qradar-observability-frontend@0.1.0 lint
> next lint
✔ No ESLint warnings or errors
```

Typecheck, exit 0. `tsc --noEmit` printed nothing else:

```
> qradar-observability-frontend@0.1.0 typecheck
> tsc --noEmit
```

Vitest, exit 0:

```
 Test Files  237 passed (237)
      Tests  2848 passed (2848)
   Start at  23:59:57
   Duration  40.99s (transform 30.23s, setup 169.76s, import 79.81s, tests 241.51s, environment 325.61s)
```

Build, exit 0. This gate build did not set the fixture API URL. A later capture build did, and a final `npm run build` with that URL was run after the before-header capture so `frontend/.next` matches the fixed tree. That restore build also exited 0. It is not a substitute for the gate lines above.

```
> qradar-observability-frontend@0.1.0 build
> next build
 ✓ Compiled successfully in 3.1s
 ✓ Generating static pages (25/25)
```

## Inherited from Sonnet

Kept: the short FAILED sentences and their new tests; the scroll-driven sticky rule and the `grid-auto-rows: auto` fold; `MostMatched`'s `(lower bound)` form; the shortened placeholders; the `max-height: 820px` nav tightening; `HealthDetection`'s upper-case verdicts; the source page passing the band; the `badge` markup in both `HomeDashboard` meta blocks; the new test files.

Changed: `statePillText` from underscore-to-space to `toUpperCase()`, and the assertions that expected `INSUFFICIENT DATA`. `stateMeaningAgainst` so a missing band number does not say `not inside`. The band tests so they require that distinction. `dashboard.css` so the template description is its own line at 1440 (Sonnet's markup still shared the line when the row was wide). The `statePillText` test title, which still said the underscore was read as a space.

Reverted: nothing.

## What the screenshots show

Official sets, outside the repository:

- Before, 57 PNGs: `/home/efe/Documents/QRadar-Dash-work/ui59/png/before/`
- After, 59 PNGs: `/home/efe/Documents/QRadar-Dash-work/ui59/png/after4/`

Each of these scenes is `{scene}-1440x900.png`, `{scene}-1280x800.png` and `{scene}-1024x768.png` in both directories: `p1-ips-incident`, `p1-ips-lifecycle`, `p1-waf-incident`, `p1-waf-lifecycle`, `p1-waf-endpoints`, `p1-waf-t098`, `p2-failed`, `p3-anomalies`, `p3-anomalies-scrolled`, `p4-source-cards`, `p5-source-outside-band`, `p6-most-matched`, `p7-sources`, `p7-rules`, `p7-offenses`, `p7-catchall`, `p7-log-sources`, `p9-detection-health`, `p10-template`. After also has `p3-overflow-880x768.png` and `p3-overflow-end-880x768.png`. `measurements.json` sits beside each set. Older `png/after`, `png/after2`, `png/after3` and `png/mid` are earlier captures and are not this pair. `png/headers` and `png/before-p10` are the scroll-0 retakes that were copied over the matching files in `after4` and `before`.

Document width equals the viewport on every measured shot. No horizontal page overflow at the three spec sizes.

Opened and read:

- Before `p1-ips-lifecycle` at 1440: lifecycle pills read `Normal`, `Candidate`, `Open`.
- Before `p1-waf-incident` at 1440: the capture had scrolled the title under the top bar. The DOM pill list for that file is `Open`, `HIGH`, `Normal`, `Open`.
- After `p1-waf-incident` at 1440 and `p1-ips-incident` at 1024, retaken at scroll 0: the identity pill reads `OPEN` beside `HIGH`. IPS lifecycle pills in the same DOM list are `NORMAL`, `CANDIDATE`, `OPEN`. The search field reads `Search sources, rules, anomalies` in full. At 1024 the sidebar reaches IPS sources with no scroll (712/712).
- After `p1-waf-lifecycle` and `p1-ips-lifecycle` measurements: the same upper-case pills. Before those files the pills are title case.
- After `p1-waf-t098` at 1440: the identity pill is `OPEN`. Before it is `Open`.
- `p1-waf-endpoints` at 1440, after: the t098 fixture has no source `…0a1`. The page says `This WAF source does not exist.` The endpoint pills are covered by `WafEndpointsTable.test.tsx`, not by this PNG.
- Before `p2-failed` at 1280: What changed uses the long sentence that includes `lifecycle below are unaffected`. The banner states the failure in full. The Evidence tab says `No dimension analysis is stored for this anomaly`.
- After `p2-failed` at 1280: What changed says `No contributors: evidence collection failed (see the notice below).` The banner still reads `FAILED. Contributor evidence collection failed. Detection metrics and lifecycle data remain available, and this says nothing about what happened during the anomaly — it is worth retrying.` The Evidence tab says `Evidence collection failed; see the notice above.`
- Before `p3-anomalies` at 1024: the top search is cut to `Search sources, rules, offenses, anom...`. List pills are already `OPEN`. The sticky cell has the inset rule while the table is not scrolling.
- After `p3` at 1440, 1280 and 1024: shadow `none`, clip list empty, including the opened More-filters shot.
- After `p3-overflow` and `p3-overflow-end` at 880: the Open column stays pinned. At the start the right side of the table is cut and the computed shadow is the 1px inset rule. At the end the actions column is fully in view and the computed shadow is transparent. The 1px rule is the computed style. The search field at 880 still clips (`Search source...`).
- Before and after `p4` / `p5` cards at 1024: four cards, each 117px. After `p5` at 1024 shows `NORMAL by the detector's verdict for this weekday and hour; the observed value shown here is not inside the expected band.` beside Observed `0.00` and Expected `39.40`. Before that shot the sentence says the volume is within the expected band beside the same numbers. The `p5` capture scrolls the source title under the top bar. The sentence and the four cards are on screen. The search placeholder is complete, and the sidebar fits.
- Before `p6` at 1280: the third rule's match rate reads `≥ < 0.01`.
- After `p6` at 1280: that cell reads `< 0.01 (lower bound)`, wrapping so `bound)` is the second line inside the cell. It is not clipped off the page. Neighbouring lower bounds still use `≥`.
- Before `p7` list pages at 1024: the clipped control is the top search. Page-level placeholders are not in the clip list.
- After those pages at 1024: clip list empty, sidebar 712/712.
- Before `p9` at 1024: `Other statuses: UNCOVERED 9 · Degraded 5`, with rows `Healthy`, `Degraded`, `SILENT`.
- After `p9` at 1024: `Other statuses: UNCOVERED 9 · DEGRADED 5`, rows `HEALTHY`, `DEGRADED`, `SILENT`. Search placeholder complete. Sidebar fits. Counts are right-aligned.
- Before `p10`, retaken at scroll 0: at 1440 and 1280 the header is one line, `Template, not saved 24 h view of fleet volume, source health, anomalies and offenses. Times are UTC.` At 1024 those two sentences already wrap, and the first line is ordinary text, not a badge. The top search at 1024 is clipped (`Search sources, rules, offenses, ano...`). Sidebar scroll height is 754 against 712.
- After `p10` at 1440, 1280 and 1024, retaken at scroll 0: `Template, not saved` is a badge on its own line. The next line is `24 h view of fleet volume, source health, anomalies and offenses. Times are UTC.` The permission note is the line under that. At 1024 the search placeholder is complete and the sidebar fits.

The ui52 shell status reads `Problem` / `Backend unreachable` because that fixture's health check fails. The incident pages still render.

Chrome and Next were started by PID and stopped by PID (`headers` pids 1773959 and 1773961, `before-p10` pids 1780727 and 1782111, and the earlier full before/after pair). Ports 3159, 8159 and 9159 were free at the end. The before tree was reversed with `patch -R` and reapplied with `patch`. `cmp` of the patch against `git diff` printed `ROUND_TRIP_OK`. No `git checkout`, `restore`, `stash` or `reset`.

## Open points

- `components/home/RecentAnomalies.tsx` and `components/home/TopSources.tsx` still render lifecycle pills through `stateLabel` (title case). They are outside the allowed files, so they were not edited. The anomaly list, the WAF and IPS investigations, the WAF endpoint table and `RecentIpsAnomalies` use the upper-case form.
- Chart transition labels in `WafInvestigation` and `IpsInvestigation` still use `stateLabel`. The lifecycle pills beside them use `statePillText`.
- The incident summary still has its own full FAILED sentence. The task names the three places on the page. `summarize.ts` was left alone.
- `page.layout.test.tsx` is the one assertion edit outside the allowed lists, as listed above.
- Browsers without `animation-timeline` draw no sticky rule. `prefers-reduced-motion` forces a near-zero animation duration in this stylesheet, which can drop the rule for those users.
- The 2×2 card change is not visible on the source fixture: all four cards are 117px before and after.
- The anomalies table does not overflow at 1024, 1280 or 1440, so the "while it scrolls" proof is the 880×768 pair.
- The WAF endpoint capture does not show the table. t098 has no profile for that source id.
- Below 1024 the top search can still clip. Seen at 880.
- `npm ci` was not run.
