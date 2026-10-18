
# PW-8 report (Sonnet, after the Codex snapshot)

Authoritative spec: docs/tasks/PW-8.md in this worktree (items 1-7).

## Found wrong in the inherited work
- Codex's report said typecheck was clean. At the snapshot `npm run typecheck` had 14 errors: `log_source_id` was
  nullable in `lib/api.ts` but 9 callers still assumed a string (item 6 "check every caller" was not done).
- `SubjectPicker.test.tsx` expected "fresh pin becomes selectable after the next" while the component said
  "A new pin can be used in a rule after the next property-watch collection" (test failed at the snapshot).
- The oversized-group reason was only inside a native `<option>`, so it was invisible on screen; now listed in the page.
- Delete 409 read "... first Detach these rules first: ..." (message repeated, no stop); now "...; Rules to detach first: ...".
- An empty preview still drew a chart with a "Floor 90 %" panel and colliding axis labels; the chart is no longer drawn when
  no value was measured (the explanation carries it).
- The rate-limit text did not say when to retry; now "rate limited to 5/minute. Try again in about a minute ...".
- A just-pinned value was only explained when no pin was listed; the hint now also shows when other pins are listed.
- Items 6/7 status: honesty line was already moved above the chart (test pins it); 409 body shape handled.

## Files changed (beyond Codex's snapshot)
frontend/src/components/propertywatch/{WatchEditor,WatchPickers,WatchActions}.tsx, propertywatch.css and their tests;
components/customrules/SubjectPicker.tsx + test; null-source callers outside the allowed list, needed by item 6:
app/anomalies/[id]/page.tsx, components/behavior/AnomalyRows.tsx, components/catalog/ipsAnomalies.ts,
components/shell/TopSearch.tsx, components/waf/wafLabels.ts, lib/behaviorOverview.ts, lib/home/recentAnomalies.ts.
A PW row now names its watch where a source name was shown; a PW row is skipped in the per-source attention table
(it has no source; before it would have put `null` in the claimed set); a PW row on the Home list links to its watch.

## Gates (from frontend/)
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit` with no output (0 errors)
- `npx vitest run --no-cache`: `Test Files  1 failed | 306 passed (307)` / `Tests  1 failed | 3395 passed (3396)`.
  The one failure is the known `src/widgets/registry.test.ts` (permissions.ts lacks the two Phase G permissions; fixed on agent/pw9).
- `npm run build`: `✓ Compiled successfully`, `✓ Generating static pages (35/35)`.
- `npm ci` not rerun (node_modules were installed).

## Break-it (code broken, named test fails; code restored)
oversize disabled / oversize reason: "searches real options ... disables an oversized group"; 422 reasons: "renders every 422 reason
exactly as sent"; 409 version: "does not overwrite a version conflict"; 409 rules: "confirms deletion ... dependency 409"; rate limit: "says when to
retry"; scope silence: "designs the scope silence empty preview"; collection off: "shows collection off ..."; read-only AQL: "renders AQL only
as escaped read-only code"; writer permission named: "hides the editor from a reader"; honesty above chart: "puts the honesty line in the values tab";
pin reason: "validates quotes, backslashes ..."; PW picker pinned only: 2 tests; fresh-pin wording: "explains that a fresh pin waits"; enable
confirmation: "makes enable explicit ...". Three first attempts were NOT caught (scope-silence wording, collection-off banner, Read-only label);
tests tightened, re-broken, now caught.

## Screenshots (/home/efe/Documents/QRadar-Dash-work/pw8/, `<name>-<w>x<h>.png` first screen and `-full.png` whole page; all three viewports)
Fixture: fixture_api.py (real payload shapes), capture: capture.mjs. Page overflow checked in every run: scrollWidth == clientWidth at all
36 captures (1425/1425, 1265/1265, 1009/1009).
- new-empty: empty form, five sections, preview beside it at 1440/1280, below the form at 1024; "No preview has run" state. Large gap under the title (shared PageHeader).
- new-group-walk-preview: group scope "resolves to 37 sources", "Groups walked: DMZ firewalls -> DMZ edge", oversized Datacentre listed disabled with the number
  and what to do, one pin, summary sentence, MEASURED banner, chart, coverage 89.0 % / remainder 474 events, table with the hostile `<img>` value shown as text,
  two collapsed read-only AQL panels.
- new-oversize-group, new-two-properties (types 2 of 3, "Not in the catalog", disabled `user;name` with reason; both listboxes show open at once because
  the script cannot blur, an artifact), new-422-reasons (three reasons as sent), new-preview-empty (scope silence, no chart), new-preview-lower-bound,
  new-rate-limited.
- watch-edit-enable: Edit watch disclosure open, enable confirmation naming switch "on", 6 of 10 slots, 9 of 20 searches.
- watch-delete-409: red confirmation plus the refusal naming two rules as links. watch-reader: editor hidden, `property_watch:write` named.
- rule-new-pw-kind: "Property watch value" kind, watch, pinned value `svc-backup`, pin-first link and the "about an hour" sentence.

## Open points
- Preview chart shows one 60-minute point per value (no band): honest but thin. If analysts expect a series it needs a backend that serves one.
- The scope "groups walked" appears only after the first save (the API serves it per stored watch). Before it, the form says so.
- Fixture returns a group walk for a type scope too (fixture artifact; the real API sends none).
- Editing an enabled watch saves it disabled (spec: "Save as disabled and preview"); the preview panel says so, but there is no extra warning at the Edit button.
- Scope picker input has `role=combobox` over a native select; screen-reader semantics are approximate.
- The nine caller edits touch files outside the allowed list; each is a null-guard or a fallback label.
- No real-stack (backend) run; all behaviour was verified against the fixture and Vitest.

## Round 1
1. Preview chart removed (PreviewOut has one aggregate per value). The preview is: outcome banner, window, total/parsed/coverage/remainder,
   ranked values table, per-type reading, read-only AQL. `previewSeries`, its test and the chart sentences are gone; tests assert no chart text.
2. Detector copy: drop is "A value's exact count fell below its seasonal expected range."; new-combination is "A pair of values, each seen
   before, appeared together for the first time." The other three claims re-read, unchanged. Test pins both.
3. Delete 409: the confirmation closes on refusal; the refusal is a `banner crit` notice with the rule links.
4. Enabled watch: a one-time warning note at the top of the Edit form ("Saving here disables it until you enable it again").
5. Scope filter is `type=search` (searchbox), no combobox role, `aria-controls` -> the select, and a visible `role=status` line
   ("3 log source group options listed.").
6. Outside the allowed list, accepted by the orchestrator: app/anomalies/[id]/page.tsx, components/behavior/AnomalyRows.tsx,
   components/catalog/ipsAnomalies.ts, components/shell/TopSearch.tsx, components/waf/wafLabels.ts, lib/behaviorOverview.ts,
   lib/home/recentAnomalies.ts.
Found by the new screenshots: removing the chart dropped the only import that brought `propertywatch.css` onto /behavior/watches/new, so the whole
form rendered unstyled (page overflow 1637/1425). WatchEditor.tsx and WatchActions.tsx now import the stylesheet; `styles.test.ts` pins it.
Gates: lint clean; typecheck clean; vitest `Test Files  1 failed | 307 passed (308)` `Tests  1 failed | 3398 passed (3399)` (only the known registry test);
build `Compiled successfully`, `Generating static pages (35/35)`. All 13 scenarios x 3 viewports recaptured, no page overflow.

STATUS: FINAL
