# UI-1.3 report: sidebar navigation and shared components (UI-1.3 + UI-1.4)

## 1. Files changed
- `frontend/src/lib/nav.ts`: regrouped to UR.3.2 (unlabeled Dashboard; Behavior, Fleet, Detection, Operations, Administration). "SOC Overview" renamed "Dashboard", same route `/`. No entry added or removed otherwise.
- `frontend/src/lib/nav.test.ts`: Part 1 edits (listed below).
- `frontend/src/components/shell/AppNav.test.tsx`: Part 1 edits (listed below). `AppNav.tsx` is unchanged.
- `frontend/src/app/globals.css`: nav selection and hover; table header rule and row hover; button hover/disabled/focus; shared input, select and textarea base (zero specificity via `:where()`) with `--focus` ring; `accent-color` for radios and checkboxes. The `.nav-group-supporting` size/colour rules were removed (class kept in markup, now unstyled), so every group has equal weight.
- `frontend/src/app/tokens.css`: added `--control-accent: var(--neutral)`, with its comment.
- `docs/tasks/UI-1.3.report.md`: this file.

## 2. What I did
- Sidebar: active item is `--surface-2` with a 2 px `--neutral` left rule and `--text`; hover `--surface-3`; `aria-current` unchanged. No icons, no top bar, no heading, brand untouched.
- Most of Part 2 (primary neutral button, secondary charcoal button, pills, notices, banners, cards) was already in place from UI-1.1 and was verified, not rewritten. Changes: header separator `--border`, hover `--surface-3`, no hover on disabled buttons, explicit button focus ring, native control accent neutral, shared control base. Pills: `sevTone()` untouched, no orange added.
- No Part 2 test edit; all existing tests pass.

### Test edits (all of them)
`nav.test.ts`
1. Old: `it("leads with the four behavioral destinations")` asserting NAV[0] label null with items Overview, Anomalies, Sources, WAF behavior.
   New: `it("leads with an unlabeled group holding the Dashboard")`: NAV[0].label null and items equal [{ "/", "Dashboard" }]. Intent kept: the first group is unlabeled and leads.
2. New test `groups the four behavioral destinations first among the labeled groups`: NAV[1].label "Behavior", same four items. Intent kept: behavioral destinations come first (among labeled groups).
3. Old: `separates supporting capabilities into their own group`: NAV[1] label "Platform" with 11 items. New: same name pattern, asserts NAV[2..4] labels Fleet, Detection, Operations and their item lists (same 11 labels, regrouped; Dashboard moved out). Intent kept: supporting capabilities are separate from behavioral.
4. Administration test: `NAV[2]` changed to `NAV[5]` (two occurrences). The list still equals today's five, unchanged.
5. `links to no route that does not exist`: unchanged, plus `expect(hrefs).not.toContain("/reports")`.

`AppNav.test.tsx` (only group labels removed by the regroup, plus one link name)
1. `getByRole("list", { name: "Platform" })` became `name: "Behavior"` and `toHaveTextContent("Platform")` became `"Behavior"`; comment reworded "A labeled group". Intent kept: a group label is associated with its list via aria-labelledby.
2. `/offenses/4812` test: `name: "Platform"` became `name: "Detection"` (variable `platform` renamed `detection`). Intent kept: the owning entry of a supporting group is marked from a detail route. Offenses now lives in Detection.
3. `never marks the root entry from a nested route`: `getByRole("link", { name: "SOC Overview" })` became `{ name: "Dashboard" }`. This is a link name, not a group label: the rename the spec mandates removes the old name, so the assertion could not otherwise find the link. Intent kept: "/" is never marked from a nested route.

## 3. Gates (frontend/)
- `npm run lint`: "✔ No ESLint warnings or errors"
- `npm run typecheck`: tsc --noEmit, no output, exit 0
- `npm test`: "Test Files  98 passed (98)" / "Tests  1604 passed (1604)"
- `npm run build`: "✓ Compiled successfully in 6.3s"
Backend not touched; no backend gate run.

## 4. Screenshots
Headless Chrome (focus emulation on for the focus capture), fixture API on 4813, app on 3813 (built with the fixture API URL). All 32 routes (UI-R0 list plus /admin/outage-policy and /fleet/catchall) at 1440x900, 1280x800, 1024x768, plus three state captures (focus-input, error, empty) at the same viewports: 105 captures. **Overflow: `scrollWidth === clientWidth` in all 105, none overflowed.**
States: disabled primary-style buttons ("Review and save", "Copy") and checked radios on /admin/waf-sources/[id]; every severity pill (CRITICAL..INFO) via alerts fixture (an INFO alert was added); error state = /offenses/9999 (fixture 404); empty state = /rules?search=zzz; focus = the rules search input.

### What I saw (1024x768 PNGs opened)
- `/`: Dashboard is the active sidebar item: raised surface, light left rule, no blue. Cards, tokens as before.
- `/behavior`: Overview active in the Behavior group; group labels are small uppercase text-3; pills severity-toned, INSUFFICIENT_DATA neutral; table headers quiet. No overflow.
- `/anomalies/[id]`: Anomalies active; comparison block, chart and tabs intact; secondary "View source behavior" button charcoal with edge.
- `/fleet`: Fleet active (not Fleet sources); table rows separated by hairlines; window toggle selected state neutral.
- `/admin/waf-sources/[id]`: inputs are charcoal with edge; disabled buttons clearly dimmed; checked radios render neutral grey, not blue.
- `/admin/search-cadence`: selects charcoal; table fine.
- focus capture: the search input shows a 2 px light ring. Note: the 1024 sidebar is taller than 768, so it scrolls inside itself (already so before); Administration is below the fold of the sidebar, and was not seen in a capture, only the first screen.
- Not opened by me: the other routes and the error/empty state PNGs (overflow checked programmatically only).

### PNG paths by route (root: /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/..., listed in full)
- /: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/root/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/root/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/root/1024x768.png
- /admin: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin/1024x768.png
- /admin/classification-rules: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_classification-rules/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_classification-rules/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_classification-rules/1024x768.png
- /admin/ips-sources: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_ips-sources/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_ips-sources/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_ips-sources/1024x768.png
- /admin/ips-sources/11111111-1111-1111-1111-111111111111: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_ips-sources_11111111-1111-1111-1111-111111111111/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_ips-sources_11111111-1111-1111-1111-111111111111/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_ips-sources_11111111-1111-1111-1111-111111111111/1024x768.png
- /admin/search-cadence: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_search-cadence/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_search-cadence/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_search-cadence/1024x768.png
- /admin/waf-sources: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_waf-sources/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_waf-sources/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_waf-sources/1024x768.png
- /admin/waf-sources/11111111-1111-1111-1111-111111111111: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_waf-sources_11111111-1111-1111-1111-111111111111/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_waf-sources_11111111-1111-1111-1111-111111111111/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_waf-sources_11111111-1111-1111-1111-111111111111/1024x768.png
- /alerts: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/alerts/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/alerts/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/alerts/1024x768.png
- /alerts/bbbbbbbb-0000-0000-0000-000000000000: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/alerts_bbbbbbbb-0000-0000-0000-000000000000/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/alerts_bbbbbbbb-0000-0000-0000-000000000000/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/alerts_bbbbbbbb-0000-0000-0000-000000000000/1024x768.png
- /anomalies: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/anomalies/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/anomalies/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/anomalies/1024x768.png
- /anomalies/aaaaaaaa-0000-0000-0000-000000000000: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/anomalies_aaaaaaaa-0000-0000-0000-000000000000/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/anomalies_aaaaaaaa-0000-0000-0000-000000000000/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/anomalies_aaaaaaaa-0000-0000-0000-000000000000/1024x768.png
- /behavior: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior/1024x768.png
- /behavior/sources: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior_sources/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior_sources/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior_sources/1024x768.png
- /behavior/sources/11111111-1111-1111-1111-111111111111: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior_sources_11111111-1111-1111-1111-111111111111/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior_sources_11111111-1111-1111-1111-111111111111/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior_sources_11111111-1111-1111-1111-111111111111/1024x768.png
- /behavior/waf: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior_waf/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior_waf/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior_waf/1024x768.png
- /behavior/waf/sources/11111112-1111-1111-1111-111111111111: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior_waf_sources_11111112-1111-1111-1111-111111111111/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior_waf_sources_11111112-1111-1111-1111-111111111111/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/behavior_waf_sources_11111112-1111-1111-1111-111111111111/1024x768.png
- /config-changes: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/config-changes/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/config-changes/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/config-changes/1024x768.png
- /coverage: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/coverage/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/coverage/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/coverage/1024x768.png
- /fleet: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/fleet/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/fleet/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/fleet/1024x768.png
- /fleet/sources: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/fleet_sources/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/fleet_sources/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/fleet_sources/1024x768.png
- /log-sources: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/log-sources/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/log-sources/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/log-sources/1024x768.png
- /log-sources/11111111-1111-1111-1111-111111111111: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/log-sources_11111111-1111-1111-1111-111111111111/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/log-sources_11111111-1111-1111-1111-111111111111/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/log-sources_11111111-1111-1111-1111-111111111111/1024x768.png
- /offenses: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/offenses/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/offenses/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/offenses/1024x768.png
- /offenses/4001: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/offenses_4001/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/offenses_4001/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/offenses_4001/1024x768.png
- /rules: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/rules/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/rules/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/rules/1024x768.png
- /rules/cccccccc-0000-0000-0000-000000000000: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/rules_cccccccc-0000-0000-0000-000000000000/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/rules_cccccccc-0000-0000-0000-000000000000/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/rules_cccccccc-0000-0000-0000-000000000000/1024x768.png
- /rules/statistics: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/rules_statistics/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/rules_statistics/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/rules_statistics/1024x768.png
- /searches: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/searches/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/searches/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/searches/1024x768.png
- /searches/eeeeeeee-0000-0000-0000-000000000000: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/searches_eeeeeeee-0000-0000-0000-000000000000/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/searches_eeeeeeee-0000-0000-0000-000000000000/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/searches_eeeeeeee-0000-0000-0000-000000000000/1024x768.png
- /admin/outage-policy: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_outage-policy/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_outage-policy/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_outage-policy/1024x768.png
- /fleet/catchall: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/fleet_catchall/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/fleet_catchall/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/fleet_catchall/1024x768.png
- focus-input /rules?search=zzz: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/state-focus-input/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/state-focus-input/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/state-focus-input/1024x768.png
- error /offenses/9999: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/state-error/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/state-error/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/state-error/1024x768.png
- empty /rules?search=zzz: 1440x900 overflow=none; 1280x800 overflow=none; 1024x768 overflow=none
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/state-empty/1440x900.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/state-empty/1280x800.png
    /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/state-empty/1024x768.png
## 5. Open points
- Fixture 404s only for the deliberate /offenses/9999. Capture kit lives outside the repo; all processes (fixture, app, Chrome) were stopped.
- The sidebar group labels remain uppercase (kept from UI-1.1; the "not uppercase" rule in the spec is for table headers).
- Regrouped nav means /alerts now sits in Detection after Offenses (UR.3.2 order).

## Review round 1

Defect: the regrouped sidebar was taller than the viewport and hid the active item.

### Changes
- `globals.css` (tokens only): sidebar padding `--sp-5` (was `--sp-6`); brand margin `--sp-4` (was `--sp-6`); group spacing `--sp-2` margin and padding above the separator (was `--sp-6`/`--sp-5`); group label margin `--sp-1` with `--lh-tight`; item padding `--sp-1` vertical (was `--sp-2`). Text sizes unchanged. Items measure 28 px high (spec floor 28 px).
- `AppNav.tsx`: a `useEffect` keyed on the pathname calls `scrollIntoView({ block: "nearest" })` on the `aria-current` link, guarded for jsdom (`typeof ... === "function"`). The nav gets a ref. `nearest` moves only the sidebar's own scroll.
- `AppNav.test.tsx`: new `AppNav scroll into view` describe, two tests: the stubbed `scrollIntoView` is called once with `{ block: "nearest" }` and on the Anomalies link for `/anomalies/abc-123`; it is not called on an unowned path.
- Break-it check: replaced the `scrollIntoView` call with a comment; the first new test failed ("1 failed | 11 passed"); restored, 12 pass.

### Gates
- `npm run lint`: "✔ No ESLint warnings or errors"
- `npm run typecheck`: clean
- `npm test`: "Test Files  98 passed (98)" / "Tests  1606 passed (1606)"
- `npm run build`: "✓ Compiled successfully in 5.8s"

### Measured in Chrome (live viewport, after load)
Sidebar scrollHeight / clientHeight, active link top-bottom, page scrollY (always 0, so the page never scrolls), `scrollWidth === clientWidth` held in all 12:
- 1440x900: sidebar content fits (900/900, no inner scroll). Active item visible: /=74-102, /rules/statistics=485-512, /admin/search-cadence=667-694, /admin/ips-sources=777-804.
- 1280x800: content 820, 20 px inner scroll. Active visible: /=74-102, /rules/statistics=485-512, /admin/search-cadence=667-694, /admin/ips-sources=773-800 (scrollTop 4, brought in by the effect).
- 1024x768: content 838 (brand wraps), inner scroll. Active visible: /=92-120, /rules/statistics=503-530, /admin/search-cadence=685-712, /admin/ips-sources=741-768 (scrollTop 54, brought in by the effect).
Not fully fitting at 1280x800 and 1024x768, as expected; the effect makes the active item visible there.

### What I saw
- Opened `admin_search-cadence/1440x900.png`: all six groups and 21 entries are visible, "Search cadence" is highlighted in Administration, and IPS sources is the last entry with empty space below.
- Opened `admin_ips-sources/1024x768.png`: the Administration group shows down to "Outage alerts" and "IPS sources" is not in the picture. This PNG is a full-page capture starting at the sidebar's top, so it does not show the sidebar's inner scroll; the numeric check above (top 741, bottom 768 in the live viewport) is the evidence that the active item is visible there. I state this rather than claim the PNG shows it.
- Active item visible at 1440: yes (PNG and numbers). At 1024: yes by the numbers, not by that PNG.

### PNG paths (re-captured, 4 routes x 3 viewports; the other routes' PNGs from the first round are from the earlier, taller sidebar)
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/root/1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/root/1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/root/1024x768.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_search-cadence/1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_search-cadence/1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_search-cadence/1024x768.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_ips-sources/1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_ips-sources/1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/admin_ips-sources/1024x768.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/rules_statistics/1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/rules_statistics/1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui13/png/rules_statistics/1024x768.png
