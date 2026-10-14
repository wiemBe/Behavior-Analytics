# UI redesign plan — charcoal system, a new home, a widget dashboard (UI-R0)

Status: **proposal, 2026-10-14. Nothing is built. The user approves this plan before any code.**
Written by Sonnet for the orchestrator, under [`tasks/UI-R0.md`](tasks/UI-R0.md). It records the
user's request ([`PLANNER.md`](PLANNER.md) "Resume here", seventh session) and decision **D-10**
(§4) as binding, and it rests on the facts in UR.0.3, each read from the code on
`feat/phase-ce` (`268f062`) and, for `/fleet/catchall`, from the finished worktrees `t070b` and
`t069`.

Where this plan proposes something the user has not decided, it says **proposal** or **ruling
proposed**. The orchestrator decides the rulings (UR.9). The user answers the questions in UR.10.

## UR.0 What the user asked, and what this plan decides

### UR.0.1 The request

- Apply the palette and design of [`UI_Suggestions/UI_Suggestion.md`](UI_Suggestions/UI_Suggestion.md)
  and of the [home screenshot](UI_Suggestions/QRadar%20Observability%20Security%20Dashboard.png)
  **everywhere**.
- It is **mostly UI polish**. No existing work is overhauled without asking.
- Home widgets with real logic behind them are welcome.
- **Binding:** the charcoal palette and the semantic colours; no blue- or purple-tinted
  background; the home layout and composition follow the screenshot; D-10; `AGENTS.md` §3a and
  [`PRODUCT-UX-GUIDELINES.md`](PRODUCT-UX-GUIDELINES.md) except where the request changes them.
- **D-10 (the user's answers):**
  1. The full widget system now: catalog, drag and drop, resize, multiple dashboards per user
     persisted in the DB (new table, migration, API), with `react-grid-layout`.
  2. The world map: a **new geo collection**, live-validated first, then a country-level aggregate
     under the search cadence. No raw IP is kept.
  3. "Events by category" becomes **EPS by source class**: top 4 classes plus "Other".
  4. Top bar on **existing APIs only**: search over existing lists; "Live" becomes auto-refresh
     with the last-updated time; the bell shows the open-alert count and links to `/alerts`.

### UR.0.2 What is adapted from the suggestion, and why

The suggestion is a generic brief. Where the product already answers a point better, the product
wins. Nothing here weakens the palette or the home layout.

| Suggestion says | This plan | Reason |
|---|---|---|
| Create a new "Log Sources", "Source Detail", "Anomaly Investigation", "Detection Coverage", "Reports" sidebar | Keep today's routes and names; regroup them (UR.3). No "Reports" entry. | `lib/nav.ts`: "a navigation entry is a promise that something is on the other side of it". No `/reports` route exists. |
| Log-source page: "bulk operations where supported" | None added. | The backend has no bulk log-source write. `PATCH /log-sources/{id}` is per source. |
| "Detection confidence" and "Evidence completeness" on the anomaly page | Keep exactly what `/anomalies/[id]` has. | They exist and are pinned by tests (`IncidentMetrics`, `EvidenceBanner`). Restyle only. |
| "User profile and settings" in the top bar | Not built (UR.3.4). | No identity endpoint exists, and OIDC is `501` until Phase 4. Showing a name would be invented. |
| "Use existing persistence; do not add a new database" | Same PostgreSQL, two new tables (UR.5.5). | D-10 already decided a table and a migration. |
| Reuse the charting stack | Keep ECharts, modular imports. | Three chart components already do it (`VolumeChart`, `WafRequestChart`, `ResultTrend`/`BucketChart`). |
| "Animations" limited | None, except what the browser does natively. | `AGENTS.md` §3a. |
| Muted text `#73757B` | Raised to `#8C8E94`; `#73757B` kept for disabled text only (UR.2.2). *Superseded by D-11: `--text-3` shipped as `#73757B`, below AA by the user's decision — see UR.2.2.* | `#73757B` fails WCAG AA on every surface (3.70:1 on `--surface`). |

### UR.0.3 Facts about the repository that shape this plan

Read from the code. Each is cited so it can be re-checked.

1. **Stack.** Next 15.5.22, React 19.2.7, ECharts 5.5.1 imported modularly. Vitest 2.1.8 + jsdom:
   92 test files, about 1,460 `it` cases. `echarts-for-react` 3.0.2 is in `package.json` and
   **imported nowhere** (`grep` over `src/` finds no use). It is dead weight; remove it in
   Phase 6, not before.
2. **Tokens.** `src/app/tokens.css` (163 lines) holds every colour. `src/app/globals.css`
   (2,711 lines) holds **zero** colour literals (`grep` for `#hex` and `rgb(` finds none), so
   the palette change is almost entirely a `tokens.css` edit. `components/ui/controls.css`
   (130 lines) is imported by component, which is the precedent for feature CSS outside
   `globals.css`.
3. **Today's palette is navy.** `--canvas #0a0e17`, `--surface #111726`, `--info #4f8cff`
   (blue) is the link colour, the observed series, the primary button fill and the focus ring.
   This is the identity the user wants gone.
4. **The home is not where the screenshot puts it.** `/` is "SOC Overview": nine `StatCard`s,
   no test, one error line, last in the sidebar. `/behavior` is "Behavioral overview": four
   counters, a worklist, recently resolved, a health bar; heavily tested. The screenshot's page
   is titled "Behavioral Overview" and mixes both. See collision C3.
5. **Auth.** The browser sends no credentials. `get_principal` (`app/security/auth.py`) returns an
   admin principal `local-dev` in non-production with no token, a read-only `local-token` with any
   bearer token, `401` in production under local auth, and `501` under OIDC ("implemented in
   Phase 4"). A `Principal` is `{subject, email, permissions}`. There is **no `/me` endpoint**,
   so the browser cannot learn its own permissions except by receiving a `403`.
6. **Permissions** (`app/security/rbac.py`, `app/api/router.py`): `read:overview`,
   `read:log_sources`, `read:searches`, `read:alerts`, `read:search_cadence`, `read:offenses`,
   `read:rules`, `read:coverage`, `read:providers`, `read:anomalies`, `read:fleet`; `admin:waf`,
   `admin:ips`, `admin:outage` (guard every method, reads included); writes: `log_source:write`,
   `rule:write`, `coverage:write`, `alert:ack`, `alert:resolve`, `search:write`,
   `admin:classification`, `admin:search_cadence`. `read:*` grants every `read:` permission;
   `admin:*` grants everything.
7. **Honesty is data, not decoration.** `MetricBucket.completeness` is `COMPLETE | PARTIAL |
   MISSING`; fleet figures are `null` when no complete bucket exists; evidence is `NOT_REQUESTED
   | PENDING | COMPLETE | PARTIAL | UNAVAILABLE | FAILED`; anomaly state includes
   `INSUFFICIENT_DATA` and `SUPPRESSED`. The restyle must carry all of them
   (guidelines §4, §5, §6, §10).
8. **Collection cadence.** The default for every search family is **1 hour**
   ([`SEARCH-CADENCE.md`](SEARCH-CADENCE.md)). A page that says "Live" or "real-time" is false by
   up to one cadence plus the settle time. This drives UR.3.4 and collision C6.
9. **Migration head** is `0019` on `feat/phase-ce`. T-069's `0020` is being re-chained after it
   (T-076 Part B). **A dashboards migration must chain after whatever the head is when it is
   written**; the numbers in UR.5.5 are placeholders.
10. **Two N+1 reads** the Home would hit on every auto-refresh:
    - `GET /behavior/sources` (`list_source_behavior`) runs `latest_bucket` and `baseline_cell`
      once per monitored source.
    - `GET /anomalies/summary` runs `baseline_cell` once per monitored source to count
      `insufficient_data_sources`.
    Neither is a QRadar search (both read the application database), but a widget dashboard
    multiplies them. UR.8.3 and task B-2.2 deal with this.
11. **`/behavior/sources` `observed_eps` is the latest stored bucket, whatever its
    completeness** (`latest_bucket` has no `COMPLETE` filter, and `SourceBehaviorOut` carries no
    completeness). A Home table ranking sources on it could rank a `PARTIAL` bucket as a fact.
    UR.4 uses `/fleet/summary` for rankings instead, and proposes an additive field.
12. **`GET /overview` degrades to zeros.** `OverviewService._offense_overview` returns
    `active=0, critical=0, unassigned=0` when no offense snapshot exists, and `_silent_count`
    "approximates" silence as `last_event_time IS NULL`. Both break `AGENTS.md` rule 8 if shown
    as measurements. The Home must not take its offense or silence figures from `/overview`.
13. **No fleet-wide time series exists.** Per-source series exist
    (`GET /behavior/sources/{id}/metrics`, `…/baselines`). `GET /fleet/summary?window=1h|24h|7d`
    returns per-class **window means** (`mean_eps`, `expected_eps`), not a series. The Home's
    headline chart needs a new bulk endpoint (UR.4.3, task B-2.1).
14. **Search over lists.** Server-side text search exists for fleet sources
    (`GET /fleet/sources?q=`), rules (`GET /rules?search=`) and offenses
    (`GET /offenses?search=`). Anomalies have **no** text search (filters only:
    `log_source_id`, `anomaly_type`, `state`, `severity`, …).

### UR.0.4 What I did not do or verify

- I did not run the application, take screenshots, or touch a live QRadar.
- I verified `react-grid-layout` against React 19 by installing and rendering it in a scratch
  directory (UR.5.2). **I could not verify drag and resize in a real browser**; that is the first
  acceptance item of task UI-3.2.
- I did not read every `globals.css` rule. Selector-level churn is estimated from the pinned tests
  (UR.8.1), not from the whole stylesheet.
- I did not check `aggregate_metrics` (offenses) for a no-snapshot case. UR.4.3 makes that a
  precondition of the offenses KPI.

---

## UR.1 Page inventory

Thirty-two routes: the 31 on `feat/phase-ce` when this plan was written, plus `/fleet/catchall`
(T-070b, merged since). `/admin/outage-policy` (T-070a) is merged and is in the 31.

**Columns used in each entry.**
- **Question:** the one question the route answers.
- **Data:** exact API routes (prefix `/api/v1`), and the permission, where one guards it.
- **Honesty states:** the states the page must keep distinct.
- **Changes:** what the redesign does.
- **Keeps:** the invariants, and the pinned tests that guard them.

**Rule for every entry.** The restyle changes tokens, CSS and shared components. It does **not**
rename a class that a test pins (UR.8.1), reorder a section a page test pins, or change a rendered
word that an invariant test asserts. Where a task has to edit a test, UR.7 names the file and the
assertion; nobody else may.

### UR.1.1 Behavioral group (the product's purpose, guidelines §1, §3)

**`/` — SOC Overview → becomes the Dashboard (home).**
- Question: what needs me now, across behavior, fleet, offenses and alerts?
- Data today: `GET /overview` (`read:overview`). Data after: see UR.4 (widgets).
- Honesty states: today none (one "Backend unreachable" line). After: every widget has loading,
  empty, no data, stale, error, insufficient permission (UR.5.4), plus `INSUFFICIENT_DATA`,
  `PARTIAL`, `TRUNCATED`, `NOT_COLLECTED` where its data has them.
- Changes: rebuilt in Phase 2 after the screenshot; becomes a widget grid in Phases 3 and 4.
- Keeps: nothing pinned (no test exists for it). Phase 2 adds tests for every state.
- Note: the nine counters go. They do not move to `/behavior`.

**`/behavior` — Behavioral overview.**
- Question: what needs me now (guidelines §3)?
- Data: `GET /anomalies/summary`, `GET /behavior/sources`,
  `GET /anomalies?active_only=true&limit=50&subject_kind=LOG_SOURCE` (all `read:anomalies`).
- Honesty states: `INSUFFICIENT_DATA` (never green, banner), unbaselined expected shown as an
  em dash, evidence pending versus failed.
- Changes: tokens, the KPI cards, table and pill styling. It stays a separate route from the new
  Dashboard (C3).
- Keeps: `app/behavior/page.test.tsx`: exactly four primary cards; secondary counts outside the
  primary row; no source inventory on the page; "Insufficient data" card carries its own note;
  the unbaselined count is never green; the worklist order (confirmed above candidate).

**`/behavior/sources` — Source inventory.**
- Question: which source do I want?
- Data: `GET /behavior/sources` (`read:anomalies`).
- Honesty states: state `INSUFFICIENT_DATA`, null expected, null deviation.
- Changes: table density and filter row; the page keeps local table scroll.
- Keeps: `app/behavior/sources/page.test.tsx`; no page-level horizontal overflow (guidelines §9).

**`/behavior/sources/[id]` — Source behavior.**
- Question: did this source change, and is the baseline trustworthy?
- Data: `GET /behavior/sources/{id}`, `GET /log-sources/{id}`,
  `GET /behavior/sources/{id}/metrics`, `GET /behavior/sources/{id}/baselines`,
  `GET /anomalies?log_source_id={id}`.
- Honesty states: bucket `COMPLETE | PARTIAL | MISSING`; baseline `is_reliable=false` ("Still
  learning"); a measured zero renders `0.00`, an unmeasured value an em dash.
- Changes: tokens; the chart palette; the four-metric block restyled.
- Keeps: `app/behavior/sources/[id]/page.test.tsx` (fixed order of six answers; exactly four
  primary metrics; no fifth; completeness reaches the chart); `VolumeChart.test.tsx` (null for
  non-complete buckets, `connectNulls: false`, shaded gaps, a named image with a text summary).

**`/behavior/waf` and `/behavior/waf/sources/[id]` — WAF behavior.**
- Question: which WAF source or endpoint changed?
- Data: `GET /behavior/waf/sources?since&until&limit&offset`;
  `GET /behavior/waf/sources/{id}`, `GET /behavior/waf/sources/{id}/endpoints`
  (`read:anomalies`).
- Honesty states: EXACT versus LOWER_BOUND measurement, endpoint state `null` = "no detector has
  evaluated it" (not `NORMAL`), `INSUFFICIENT_DATA`.
- Changes: tokens and tables only.
- Keeps: `app/behavior/waf/page.test.tsx`, `…/sources/[id]/page.test.tsx`,
  `components/waf/*.test.*` (14 files, including `WafRequestChart.test.tsx`).

**`/anomalies` — Triage.**
- Question: which incident do I open first?
- Data: `GET /anomalies` (filters, `since`/`until`, paging), `GET /behavior/sources` (picker).
- Honesty states: all seven lifecycle states; `SUPPRESSED` neutral; evidence status per row.
- Changes: tokens, filter chips, table; severity pill (UR.2.4).
- Keeps: `app/anomalies/page.test.tsx`, `waf.page.test.tsx`; `AnomalyFilters*.test.tsx`; active
  state derived from the explicit state, never from `resolved_at` (guidelines §5).

**`/anomalies/[id]` — Investigation (flagship).**
- Question: what changed during the anomalous interval?
- Data: `GET /anomalies/{id}`; `GET /behavior/sources/{id}/metrics`; for a WAF anomaly
  `GET /behavior/waf/endpoints/{id}/metrics` and `…/baselines`.
- Honesty states: evidence `UNAVAILABLE` versus `FAILED` versus empty `COMPLETE`; `TRUNCATED`
  withholds counts; still-running incident has no end time or duration.
- Changes: tokens, the comparison block, the timeline marker colours.
- Keeps: `app/anomalies/[id]/page.test.tsx` (hierarchy, four measures in one block, first
  viewport carries no AQL or version table, at most one contributor table, timeline order),
  `waf.page.test.tsx`, `IncidentMetrics`, `EvidenceBanner`, `DimensionExplorer`,
  `LifecycleTimeline`, `ContributorTable`, `StatusBadge` tests.

### UR.1.2 Fleet, detection and operations (supporting capabilities)

**`/fleet` — Fleet summary.**
- Question: how healthy is each class of source?
- Data: `GET /fleet/summary?window=1h|24h|7d` (`read:fleet`).
- Honesty states: a figure with no complete bucket is `null`; `buckets.partial/missing`;
  `baseline_share`.
- Changes: tokens; class table.
- Keeps: `app/fleet/page.test.tsx`, `components/fleet/FleetClassTable.test.tsx`.

**`/fleet/sources` — Fleet sources.**
- Question: which source belongs to which class, and who has reviewed it?
- Data: `GET /fleet/sources?class&review_status&has_conflict&basis&q&limit&offset`,
  `GET /classification-rules`; writes `PATCH /log-sources/{id}/classification`,
  `POST /log-sources/{id}/review` (`log_source:write`).
- Honesty states: `auto_discovered` null = unknown; `review_status` null = no classification row.
- Keeps: `app/fleet/sources/page.test.tsx`, `FleetSourcesTable.test.tsx`, `RulesManager.test.tsx`.

**`/fleet/catchall` — Catch-all senders** (T-070b, unmerged).
- Question: which unconfigured devices send to the catch-all sources, and how much?
- Data (T-069, `/fleet/catchall`): `GET /sources`, `GET /senders?review_status&limit`,
  `GET /buckets?catchall_log_source_id&since&until`; writes `POST /senders/{id}/review` and
  `/dismiss` (`log_source:write`). Read needs `read:fleet`.
- Honesty states: a bucket with no row is **not measured** (shaded, never a zero);
  `LOWER_BOUND` bucket takes the warn fill and the words "at least"; `events_24h` null.
- Changes: tokens; the page's 122 lines of CSS in `globals.css` must be re-read against the new
  tokens, because they were written against the navy ones.
- Keeps: `catchall.test.ts`, `BucketChart.test.tsx`, `SendersQueue.test.tsx`,
  `SourceVolume.test.tsx`, `page.test.tsx`: one value axis, units on every axis, UTC on the time
  axis, keyboard buttons that mirror the chart's click selection.

**`/offenses` and `/offenses/[id]`.**
- Question: which offense needs a SOC analyst? / what is this offense?
- Data: `GET /offenses?status&search&sort&limit&offset`, `GET /offenses/aggregates`;
  `GET /offenses/{id}`, `GET /offenses/{id}/history` (`read:offenses`).
- Honesty states: `magnitude`, `severity`, `credibility`, `relevance` are `number | null`.
- Changes: tokens; table; magnitude shown as a bar **with the number** beside it (colour is
  not the carrier).
- Keeps: no page test exists. Phase 5 task UI-5.5 adds a render test per state before the
  restyle (UR.7).

**`/rules`, `/rules/[id]`, `/rules/statistics`.**
- Question: which rule is unhealthy / what is this rule doing / what does the rule set cost?
- Data: `GET /rules?…&health_status&enabled`, `GET /rules/health-summary`;
  `GET /rules/{id}`, `…/health-history`, `…/capacity-history`; `GET /rules/statistics?days=`
  (`read:rules`).
- Honesty states: statistics sections are `null` with a reason, never an empty list;
  `lower_bound` match rates; truncated, failed and missing windows counted apart.
- Keeps: `app/rules/page.test.tsx`, `[id]/page.test.tsx`, `statistics/page.test.tsx`,
  `ruleStatistics.test.ts`.

**`/coverage`.**
- Question: where is detection coverage absent or degraded?
- Data: `GET /coverage/summary`, `GET /coverage/techniques`, `GET /coverage/by-rule`,
  `GET /coverage/by-data-source` (`read:coverage`).
- Honesty states: `coverage_score` and `confidence` null; `mapping_provenance` explicit versus
  inferred.
- Keeps: no page test. UI-5.5 adds one. No new coverage claim is introduced.

**`/log-sources` and `/log-sources/[id]`.**
- Question: which sources exist, and what is this one's metadata and health?
- Data: `GET /log-sources`, `POST /log-sources/sync` (`admin:*`); `GET /log-sources/{id}`
  (`read:log_sources`).
- Honesty states: `health_score` null; `health_breakdown` null.
- Keeps: no page test. UI-5.6 adds one.

**`/searches` and `/searches/[id]`.**
- Question: which stored searches exist and how did they run?
- Data: `GET /searches`; `GET /searches/{id}`, `…/versions`, `…/executions`, `…/results`;
  `POST /searches/{id}/run`, `PATCH /searches/{id}` (`search:execute`, `search:write`).
- Honesty states: execution `truncated`; result-trend gaps.
- Keeps: `ResultTrend.test.tsx`, `RunSearchButton.test.tsx`, `ScheduleControl.test.tsx`,
  `ExecutionsTable.test.tsx`. Frontend never supplies AQL to run (rule 4); the UI shows stored
  text in `CodePanel`.

**`/alerts` and `/alerts/[id]`.**
- Question: which alerts are open? / what happened on this one?
- Data: `GET /alerts?status=`; `GET /alerts/{id}`, `…/notifications`;
  `POST /alerts/{id}/acknowledge|resolve` (`alert:ack`, `alert:resolve`).
- Keeps: `AlertActions.test.tsx`. No page test; UI-5.6 adds one.

### UR.1.3 Administration

**`/admin` — hub.** Static list of links; not in the nav (pinned absent by `nav.test.ts`). Its
description says "Further sections arrive in Phase 4": stale copy, fixed in UI-5.7.

**`/admin/search-cadence`.** `GET /admin/search-cadence`, `PUT /admin/search-cadence/{family}`
(`read:search_cadence`, `admin:search_cadence`). Honesty: `last_run` null = "has not run since the
upgrade"; `searches_sent` null = not counted. Keeps `page.test.tsx`, `SearchCadenceTable.test.tsx`
(the `perm`, `nowrap`, `crit` classes are asserted).

**`/admin/classification-rules`.** `GET/POST /classification-rules`,
`PATCH/DELETE /classification-rules/{id}` (`read:fleet`, `admin:classification`). Keeps
`page.test.tsx`, `RulesManager.test.tsx`.

**`/admin/outage-policy`** (T-070a, merged). `GET /outage-policy`, `GET /fleet/summary?window=`,
`PUT/DELETE /outage-policy/classes/{class}`, `PUT/DELETE /outage-policy/sources/{id}`
(`admin:outage`, reads included). Honesty: `governed_monitored_sources` null; `silence_grace_buckets`
null = default grace. Keeps `hub.test.tsx`, `OutagePolicyManager.test.tsx` (`cell-end`),
`outage.test.ts`, `page.test.tsx` (`num` on numeric cells and headers).

**`/admin/waf-sources`, `/admin/waf-sources/[logSourceId]`.** `GET /waf/sources`,
`GET /waf/mapping-schema`, `GET /waf/candidates`, `GET|PUT /waf/log-sources/{id}/profile`,
`POST …/profile/validate|enable|disable`, `GET …/profile/history`, `GET …/property-catalog`
(`admin:waf`). Keeps `AddSourcePicker.test.tsx`, `ProfileEditor.test.tsx`, both page tests and
`components/fieldmapping/*.test.*` (validation states `UNAVAILABLE` and `FAILED` stay distinct).

**`/admin/ips-sources`, `/admin/ips-sources/[logSourceId]`.** Same shape under `/ips/…`
(`admin:ips`): `GET /ips/sources`, `GET /ips/mapping-schema`, `GET /ips/candidates`, profile
routes, `GET /ips/log-sources/{id}/property-catalog`. Keeps `ips.test.ts`,
`IpsProfileEditor.test.tsx` (`is-warn`), `AddIpsSourcePicker.test.tsx`, page tests (`ips-status`
class asserted).

**`/config-changes`.** A placeholder: heading "Configuration Changes", "Planned for Phase 3". Not
in the nav, no backend route (a `config_change` model exists, no API). Proposal: restyle only; do
not delete it (the user did not ask); ask whether it should be removed (UR.10, Q5).

### UR.1.4 Work not yet built that lands on this inventory

- **IPS analyst view** (Phase D, T-052 and after): new routes under `/behavior` for IPS sources
  and findings. No data API exists for it yet (`/ips/*` is the admin surface, `admin:ips`).
  Treated in UR.8.7.
- **`/geo`**-style pages: none planned. The map is a widget only.

---

## UR.2 Design tokens

The tokens stay the only home for literals (`AGENTS.md` §3a). Every token name in `tokens.css`
is kept, because `chartPalette.test.ts` asserts the chart's token names exist, and ~170 test
lines assert class names that `globals.css` ties to them. Values change. New tokens are added.

### UR.2.1 Mapping of every existing token

| Token | Old | New | Note |
|---|---|---|---|
| `--canvas` | `#0a0e17` | `#111214` | Application background |
| `--surface` | `#111726` | `#1B1C1F` | Primary surface |
| `--surface-2` | `#182034` | `#252629` | Elevated surface |
| `--surface-3` | `#1e2740` | `#2D2E32` | Hover surface |
| `--border` | `#232c42` | `#35363A` | Hairline |
| `--border-strong` | `#33405e` | `#6E7077` | Input edge; see UR.2.2 (3:1 rule). *Superseded by D-11: shipped `#35363A`* |
| `--text` | `#e8eef8` | `#E8E8E8` | |
| `--text-2` | `#a8b4c9` | `#96989D` | |
| `--text-3` | `#7c8aa3` | `#8C8E94` | **Not** the suggestion's `#73757B` (fails AA). *Superseded by D-11: shipped `#73757B` (UR.2.2)* |
| `--crit` | `#f85149` | `#E36565` | |
| `--crit-text` | `#ff9d97` | `#F08F8F` | |
| `--crit-bg` / `-border` | `rgba(248,81,73,.12/.34)` | `rgba(227,101,101,.12/.34)` | |
| `--warn` | `#d29922` | `#E5AD56` | |
| `--warn-text` | `#e3b341` | `#EDC27E` | |
| `--warn-bg` / `-border` | `rgba(210,153,34,.12/.34)` | `rgba(229,173,86,.12/.34)` | |
| `--ok` | `#3fb950` | `#3FBF91` | |
| `--ok-text` | `#56d364` | `#6BD6AF` | |
| `--ok-bg` / `-border` | `rgba(63,185,80,.12/.32)` | `rgba(63,191,145,.12/.32)` | |
| `--info` | `#4f8cff` | `#6FD0E0` | Cyan, **no longer blue**; the one data accent |
| `--info-text` | `#79a8ff` | `#8EDCE8` | |
| `--info-bg` / `-border` | `rgba(79,140,255,.12/.34)` | `rgba(111,208,224,.12/.34)` | |
| `--neutral` | `#8b98b0` | `#B6B8BD` | |
| `--neutral-text` | `#a8b4c9` | `#B6B8BD` | |
| `--neutral-bg` / `-border` | `rgba(139,152,176,.1/.28)` | `rgba(182,184,189,.10/.28)` | |
| `--focus` | `#8ab4ff` | `#E8E8E8` | A neutral ring, visible on the canvas and on a light button |
| `--on-accent` | `#06101f` | `#111214` | Text on a solid status fill and on the primary button |
| `--scroll-shadow` | `rgba(0,0,0,.5)` | unchanged | |
| `--sp-1…--sp-8` | 4…48 px | unchanged | |
| `--r-sm / -md / -lg` | 6 / 8 / 12 px | **4 / 6 / 8 px** | "No excessive rounded corners"; `--r-pill` unchanged |
| `--shadow` | `0 1px 2px rgba(0,0,0,.4)` | unchanged | One shadow |
| `--font` | system stack | Inter first, then the old stack (UR.2.5) | |
| `--font-mono` | system mono | unchanged | Inter has no mono |
| `--fs-xs … --fs-metric`, `--lh-*`, `--fw-*` | | unchanged | No size below 12 px |
| `--picker-list-max-h` | | unchanged | |
| `--sidebar-w` | 232px | unchanged | The screenshot's sidebar is about the same |
| `--page-max` | 1360px | unchanged for text pages; the Dashboard is fluid | |
| `--header-h` | 72px | unchanged | Used by `.page-header`; the top bar gets its own token |
| `--chart-grid` | `#1a2336` | `#2A2B2F` | |
| `--chart-axis` | `#2a3350` | `#3A3B40` | |
| `--chart-label` | `#8b98b0` | `#8C8E94` | 5.20:1 on `--surface`. *Superseded by D-11: shipped `var(--text-3)`* |
| `--chart-observed` | `var(--info)` | `var(--info)` | Cyan line, the one saturated mark |
| `--chart-expected` | `var(--neutral-text)` | unchanged | Neutral, dashed |
| `--chart-band` | `rgba(139,152,176,.16)` | `rgba(182,184,189,.14)` | |
| `--chart-gap`, `--chart-anomaly`, `--chart-marker` | warn-bg, crit-bg, neutral | unchanged references | |
| `--chart-tooltip-bg / -border / -text` | surface-2, border-strong, text | surface-2, **border**, text | `border-strong` is now a 3:1 edge, too loud for a tooltip |

### UR.2.2 Two deviations from the suggestion's table

**Superseded by D-11 (2026-10-14).** The user decided to keep the palette's original values:
`--text-3 = #73757B` and `--border-strong = #35363A` are what shipped, and `#73757B` being
below WCAG AA is accepted by that decision, not an oversight. There is no `--text-disabled`
token; disabled text uses `--text-3`. The reasoning below is kept as the record of what was
proposed and why — it is no longer the plan's intent. `tokens.css` and `lib/contrast.test.ts`
pin the shipped values: the test asserts AA for `--text`, `--text-2` and `--neutral-text`, and
deliberately not for `--text-3`.

The suggestion's table is binding except where it breaks WCAG AA. The ratios are WCAG 2.x
relative luminance, computed with a script kept outside the repository.

**Muted text** *(proposed — superseded by D-11)*. `#73757B` on the five surfaces: 4.07 (canvas),
3.93 (sidebar), **3.70** (surface), 3.29 (elevated), 2.94 (hover). It fails 4.5:1 on all of
them. Ruling proposed: `--text-3 = #8C8E94` (5.72, 5.53, 5.20, 4.62, 4.14), and `#73757B`
survives as the new `--text-disabled`, used only for disabled controls (WCAG exempts them).
`--text-3` is not used on `--surface-3` (4.14); text on a hover row uses `--text-2` (4.70).

**Input edge** *(proposed — superseded by D-11)*. `#35363A` on `--surface` is 1.41:1. WCAG
1.4.11 wants 3:1 for the boundary of an input. `--border` stays the hairline between panels
(decorative); `--border-strong = #6E7077` (3.45 on surface, 3.79 on canvas, 3.06 on elevated)
is the input and button edge.

### UR.2.3 New tokens

| Token | Value | Use |
|---|---|---|
| `--sidebar-bg` | `#151618` | Sidebar panel. `shellFixes.test.ts` pins `.layout::before` to `var(--surface)`; UR.7 edits that one assertion |
| `--text-disabled` | `#73757B` | Disabled text only. *Never added — superseded by D-11 (UR.2.2); disabled text uses `--text-3`* |
| `--primary` / `--primary-hover` | `#E8E8E8` / `#FFFFFF` | Primary button fill: neutral light, text `--on-accent` (15.30:1) |
| `--link` | `var(--info-text)` | Links in page content (was the blue) |
| `--topbar-h` | `56px` | The top bar |
| `--chart-cat-1 … -5` | `#6FD0E0 #B59A7A #E6E1D3 #7C93D1 #E08CB8` | Categorical series (UR.2.6) |
| `--chart-other` | `#73757B` | The "Other" slice |
| `--sev-critical / -high / -medium / -low / -info` | `#E36565 #A64B4B #E5AD56 #6FD0E0 #8C8E94` | **Charts only** (UR.2.4) |
| `--map-0 … -4` | `#2A2B2F #2F4A50 #3C7580 #58A5B4 #8EDCE8` | World-map ramp (UR.2.7) |
| `--widget-pad`, `--widget-gap` | `var(--sp-5)`, `var(--sp-3)` | One widget padding and grid gap everywhere |

### UR.2.4 Severity scale

`sevTone()` (`lib/api.ts`) stays: `CRITICAL` and `HIGH` → `crit`; `MEDIUM` → `warn`; `LOW` and
`INFO` → `info`; unknown → neutral. It is pinned by `statusSemantics.test.ts`, and guidelines §10
forbid a severity taking a positive tone. The screenshot shows Critical red, High orange: **there
is no orange in the palette and none is added** (it would read as `warn`). The words carry the
difference in pills. In a **chart** the five severities need five marks, so the `--sev-*` tokens
exist for charts only; `HIGH` is a darker red (`#A64B4B`, 3.03:1 on surface, ΔE 19.8 from
`CRITICAL`, 13.8 under protanopia); every severity chart also has a legend and a tooltip with the
words. CSS keyed to a severity name stays prohibited (guidelines §9); chart code reads the
tokens.

### UR.2.5 Typography: Inter, self-hosted

The product is on-premises; **no request leaves for a font, at build or at runtime.**
- **Source:** the Inter variable font, SIL OFL 1.1, taken from `@fontsource-variable/inter@5.3.0`
  (npm, published 2026-07). Copy two files into the repository and **do not add the package**:
  `inter-latin-wght-normal.woff2` (48,256 B) and `inter-latin-ext-wght-normal.woff2`
  (85,068 B). Latin-ext matters here: it carries `ş ğ İ ı`, which appear in the operator's own
  names. Add the licence text beside them (`OFL.txt`).
- **Where:** `frontend/src/app/fonts/` plus `fonts.css`, imported by `tokens.css`'s importer
  (`globals.css`). Plain `@font-face` with `unicode-range`, `font-weight: 100 900`,
  `font-display: swap`. **Not** `next/font/google` (it downloads at build time, and the build host
  may be offline). `next/font/local` is possible but cannot express `unicode-range`; plain CSS is
  simpler and testable.
- **Fallback:** the existing stack, `ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
  "Helvetica Neue", Arial, sans-serif`. Inter's tabular figures (`font-feature-settings: "tnum"`)
  already have a rule in `globals.css` (`.num`); it keeps working.
- **Container:** `output: "standalone"` copies `.next/static`, where Next emits the hashed font
  files. The `Dockerfile` needs no change.
- **Roles.** No new size token; the existing scale is used by role:

| Role | Token | Weight |
|---|---|---|
| Page title (`h1`) | `--fs-xl` (26) | `--fw-semibold` |
| Section heading (`h2`) | `--fs-md` (16) | `--fw-semibold` |
| Widget title | `--fs-base` (14) | `--fw-semibold` |
| Table header | `--fs-xs` (12), `--text-3`, not uppercase | `--fw-medium` |
| Body | `--fs-base` (14) | `--fw-normal` |
| Metadata, captions | `--fs-sm` (13) / `--fs-xs` | `--fw-normal`, `--text-2` / `--text-3` |
| Metric (KPI value) | `--fs-metric` (28), tabular | `--fw-semibold` |
| Status text, pills | `--fs-xs` | `--fw-medium` |

### UR.2.6 Chart palette: not blue-dominant, colour-blind safe

Constraints that cut the choice:
- `ok`, `warn`, `crit` are status. A category must **not** be green, amber or red, or an analyst
  reads "Firewall" as "warning". The screenshot's donut breaks this (C7). Each candidate was
  required to sit at least ΔE 25 (CIELAB) from all three.
- Each mark must be at least 3:1 against `--surface`.
- Pairwise colour distance under protanopia, deuteranopia and tritanopia (Machado 2009,
  severity 1.0) was computed for every four-colour combination of a 21-colour pool.

Result:

| Token | Hex | On `--surface` | Role |
|---|---|---|---|
| `--chart-cat-1` | `#6FD0E0` | 9.55 | cyan (also the data accent) |
| `--chart-cat-2` | `#B59A7A` | 6.38 | sand |
| `--chart-cat-3` | `#E6E1D3` | 13.04 | bone |
| `--chart-cat-4` | `#7C93D1` | 5.64 | periwinkle |
| `--chart-cat-5` | `#E08CB8` | 6.99 | rose |
| `--chart-other` | `#73757B` | 3.70 | "Other" and "unclassified" |

- **Four categories plus "Other"** (the donut): minimum pairwise ΔE 28.3 normal, 21.7 protan,
  22.8 deutan, 23.1 tritan.
- **Five categories plus "Other"**: 28.3 normal, 22.7 tritan, but **16.6 protan and 15.2
  deutan**. Usable only with direct labels. The rule: **no chart relies on hue alone**; a series is
  named in a legend with its value, in the tooltip, and (for the donut) in a list beside it.
- Two of the five are blue-family (cyan, periwinkle); the rest are warm or pink. The identity of
  the page stays neutral grey.
- A categorical series never uses `--ok`, `--warn`, `--crit`. A series that **is** a status (an
  anomaly marker, a gap band) does, as today.

### UR.2.7 World-map ramp

A choropleth of events per country is a sequential scale, and the Low-green, Medium-amber,
High-red legend of the screenshot would make "events from this country" read as "this country is
dangerous" (C7). `--map-0 … -4` is one hue stepping from the land colour to light cyan
(`#2A2B2F` land, 1.20; `#2F4A50`, 1.80; `#3C7580`, 3.28; `#58A5B4`, 6.05; `#8EDCE8`, 11.00 on
surface). The land colour is decoration, not data. A legend states the numeric breaks.

### UR.2.8 Contrast table (WCAG AA: 4.5:1 text, 3:1 non-text)

Text on backgrounds (ratio; `✓` ≥ 4.5):

| Foreground | canvas `#111214` | sidebar `#151618` | surface `#1B1C1F` | elevated `#252629` | hover `#2D2E32` |
|---|---|---|---|---|---|
| `--text` `#E8E8E8` | 15.30 ✓ | 14.78 ✓ | 13.91 ✓ | 12.35 ✓ | 11.07 ✓ |
| `--text-2` `#96989D` | 6.49 ✓ | 6.27 ✓ | 5.90 ✓ | 5.24 ✓ | 4.70 ✓ |
| `--text-3` `#73757B` (shipped, D-11) | **4.07** ✗ | **3.93** ✗ | **3.70** ✗ | **3.29** ✗ | **2.94** ✗ |
| `--text-disabled` — never added (D-11) | — | — | — | — | — |
| `--neutral-text` `#B6B8BD` | 9.44 ✓ | 9.12 ✓ | 8.58 ✓ | 7.62 ✓ | 6.83 ✓ |

`--text-3` is the palette's own `#73757B`, kept by D-11 (2026-10-14) although it is below AA on
every surface. That is the user's explicit decision, recorded in UR.2.2; the shipped
`lib/contrast.test.ts` therefore pins the value instead of asserting 4.5:1 for it.

Status text on its own 12 % tint over `--surface` (the pill) and over `--surface-3` (a hovered row):

| Text | Hex | on surface | on elevated | on its tint / surface | on its tint / hover |
|---|---|---|---|---|---|
| `--crit-text` | `#F08F8F` | 7.33 | 6.51 | 6.29 | 5.00 |
| `--warn-text` | `#EDC27E` | 10.24 | 9.09 | 8.17 | 6.44 |
| `--ok-text` | `#6BD6AF` | 9.63 | 8.55 | 7.81 | 6.25 |
| `--info-text` (link) | `#8EDCE8` | 11.00 | 9.76 | 8.53 | 6.78 |
| `--neutral-text` | `#B6B8BD` | 8.58 | 7.62 | 6.74 | 5.32 |

Base hues as marks and as small text (the suggestion's own values): `#E36565` 5.12 on surface and
**4.55** on elevated (still AA); `#E5AD56` 8.47; `#3FBF91` 7.36; `#B6B8BD` 8.58. Because they pass,
the `*-text` tints exist for continuity of class names, not rescue.

Fills and controls:

| Pair | Ratio |
|---|---|
| `--on-accent` `#111214` on `--primary` `#E8E8E8` | 15.30 |
| `--on-accent` on `--ok` / `--warn` / `--crit` / `--info` / `--neutral` solid | 8.10 / 9.32 / 5.64 / 10.51 / 9.44 |
| Selected nav: `--text` on `--surface-2` | 12.35 |
| Selection marker `--neutral` `#B6B8BD` on `--surface-2` | 7.62 (3:1 needed) |
| `--border-strong` `#35363A` (shipped, D-11) on canvas / surface / elevated | 1.55 / 1.41 / 1.25 (3:1 not met; kept by the user's decision — UR.2.2) |
| `--focus` `#E8E8E8` on canvas / surface / elevated | 15.30 / 13.91 / 12.35 |
| Pill border at 34 % alpha over surface | about 1.7 to 2.3 (decorative: the pill text is the carrier) |

The first Phase 1 task adds a unit test that reads these pairs from `tokens.css` and asserts the
thresholds, so a later edit cannot quietly break them. The test checks the invariant (ratio ≥
threshold for a named pair), not the production code. (As shipped after D-11, the test asserts
the thresholds for every pair above except `--text-3` and `--border-strong`, whose values it
pins instead.)

---

## UR.3 Shell and navigation

### UR.3.1 Layout

The screenshot puts the brand top-left over the sidebar column and a full-width top bar to its
right. Today `layout.tsx` is a two-column grid with the brand in the sidebar. The shell becomes:

```
+------------+----------------------------------------------+
| brand      | top bar (search, range, refresh, bell, status)|  --topbar-h
+------------+----------------------------------------------+
| sidebar    | main                                         |
| (sticky,   |                                              |
|  --sidebar |                                              |
|  -bg)      |                                              |
+------------+----------------------------------------------+
```

- The sidebar is painted by `.layout::before` across the whole grid height today, because a sticky
  sidebar stopped at the fold (a documented defect). The new shell keeps that technique with
  `--sidebar-bg`.
- The shell holds **no heading element** (guidelines §8). The brand is a link.
- The brand: the product name only. **The screenshot's tagline "Security Insights. Operational
  Clarity." is dropped** (C5: marketing copy). The footer card "QRadar Dashboard v2.1.0" is
  dropped: no version endpoint exists, and `2.1.0` would be invented.

### UR.3.2 Sidebar groups

All 31 existing routes stay reachable; the nav model gains one entry and is regrouped. A nav entry
exists only where a page exists.

| Group | Entries (href) |
|---|---|
| (unlabeled) | Dashboard (`/`) |
| Behavior | Overview (`/behavior`), Anomalies (`/anomalies`), Sources (`/behavior/sources`), WAF behavior (`/behavior/waf`) |
| Fleet | Fleet (`/fleet`), Fleet sources (`/fleet/sources`), Catch-all senders (`/fleet/catchall`, after T-070b merges) |
| Detection | Offenses (`/offenses`), Alerts (`/alerts`), Rules (`/rules`), Rule statistics (`/rules/statistics`), Coverage (`/coverage`) |
| Operations | Log Sources (`/log-sources`), Searches (`/searches`) |
| Administration | Search cadence, Classification rules, Outage alerts, WAF sources, IPS sources (as today) |

Notes:
- "SOC Overview" becomes "Dashboard" and moves to the top, in its own unlabeled group. It is the
  same route (`/`); this is a rename, which the suggestion says needs a migration strategy:
  the URL does not change, so bookmarks keep working. Ruling proposed (C3, UR.10 Q1 asks whether
  `/behavior` or `/` is the post-login landing).
- `/behavior` keeps the label "Overview" and sits in the **Behavior** group directly under the
  Dashboard, with equal visual weight. Guidelines §1 ("behavioral routes lead") is kept by the
  group sitting first among the labeled groups and by being fully weighted; the Dashboard is a
  cross-cutting home, not a behavioral page.
- **The IPS behavioral entries** join the Behavior group when T-052's view lands.
- Not in the nav, as today: `/admin`, `/config-changes`, every detail route.
- **Selection style:** the active item gets `--surface-2` as a background, a 2 px left rule in
  `--neutral` (7.62:1 against `--surface-2`), and `--text`; hover is `--surface-3`. Never blue. This
  matches the suggestion ("slightly elevated charcoal surface, restrained neutral indicator") and
  keeps `aria-current="page"` as the semantic carrier.
- **No sidebar icons** (C8). Text only, as today. See UR.10 Q2.
- Pinned: `AppNav.test.tsx` (landmark, no heading, unique names, one `aria-current`, owning
  section on a detail route), `nav.test.ts` (UR.7 names the three assertions it edits: the leading
  group, the Platform list, and the order). `links to no route that does not exist` is kept
  unchanged, and gains `/reports`.

### UR.3.3 Page header

`PageHeader` stays the single `h1` owner. The description stays one line of orientation and
**must not** restate the title or promise what the product does not deliver (C5, C6). The Home's
description is a fact: the window and the data age ("24 h window; latest closed bucket 13:55 UTC").

### UR.3.4 Top bar (D-10: existing APIs only)

One client component, `TopBar`, in the shell. Each element, with its data:

**Search** (centre, `⌘K` / `Ctrl+K` focuses it).
- Searches **existing lists only**, three or four requests per debounced keystroke (250 ms,
  previous request aborted, at most 5 results per group):
  - Sources: `GET /fleet/sources?q=<text>&limit=5` (`read:fleet`; server-side `q` over name and type
    name). Result links to `/behavior/sources/{id}`.
  - Rules: `GET /rules?search=<text>&limit=5` (`read:rules`) → `/rules/{id}`.
  - Offenses: `GET /offenses?search=<text>&limit=5` (`read:offenses`) → `/offenses/{id}`.
  - Anomalies: **no text search exists** (guidelines list: filters only). Proposal: match the text
    against `log_source_name` in `GET /anomalies?active_only=true&limit=200` (a client filter over
    one bounded read, cached for the session), and state in the result group "Active anomalies
    (first 200)". A server `q` is a backend gap (B-1.1, optional).
- A group whose permission is missing (`403`) is omitted silently from the results and is listed
  once in a footer line ("Offenses not searched: your role cannot read them"). It is never shown as
  "no results".
- These are reads of the application database. **No QRadar search is issued.** No command palette
  actions: results only.
- Keyboard: the combobox pattern (`role="combobox"`, `aria-expanded`, `aria-activedescendant`,
  `Esc` closes), results are links.

**Time range.**
- Pages differ: fleet uses `1h | 24h | 7d`; source detail uses `?range=`; WAF and anomalies use
  `since`/`until`; rule statistics uses `days`. A single global range that rewrites them all would
  contradict the page-local selectors the tests pin (`RangeSelector`, `WindowSwitch`,
  `WafRangeSelector`).
- Ruling proposed (C9): the top-bar range is **the default range for dashboard widgets only**,
  and it appears **only on routes that register support** (the Dashboard first). On other routes it
  is absent, not disabled. Presets are `1h`, `24h`, `7d`, as the fleet API supports. It is carried
  in `?range=` so a view is shareable.

**Auto-refresh and last updated** (replaces "Live").
- A select: `Off`, `1 min`, `5 min` (default `Off` on all routes except the Dashboard; the
  Dashboard default is `5 min`, never faster than the collection cadence makes useful).
- It shows the **time the browser last fetched** (`14:02:11 UTC`) **and** the data age where the
  page knows it ("data to 13:55 UTC"). These differ, and the second is the honest one: collection
  runs on a cadence (UR.0.3 item 8). Refresh re-fetches the widgets' resources; on server-rendered
  routes it calls `router.refresh()`.
- It pauses while the tab is hidden (`visibilitychange`) and while a dialog is open.

**Open-alert bell.**
- `GET /overview` → `alerts.open` (`read:overview`), or `GET /alerts?status=OPEN&limit=1` is not
  enough (a list, no total). The count is `alerts.open`. Link: `/alerts`.
- The badge shows the number; it uses the `crit` tone only when the count is greater than zero. A
  failed read shows no badge and a title "Alert count unavailable", **never `0`**.
- Accessible name: "Open alerts: 3" or "Open alerts: unavailable".

**Connection status** (existing health APIs). Three signals, each named, none invented:
1. **Backend and database:** `GET /health/ready` → `{status: "ready", database: "ok"}`; any failure
   is "Backend unreachable".
2. **QRadar instance:** `GET /overview` → `instance_status` (`HEALTHY | DEGRADED | UNREACHABLE |
   UNKNOWN`) and `instance_version`. **Gap:** `qradar_instance.last_health_check_at` and
   `last_error` exist in the model but are not in `SocOverview`. Proposal B-1.2: add
   `instance_last_health_check_at` additively, so "HEALTHY" can say how old that is.
3. **Collection:** `GET /admin/search-cadence` → `last_run.at` and `outcome` for
   `PHASE_A_VOLUME` (`read:search_cadence`). If the principal cannot read it, the signal is
   omitted, not shown as unknown-bad.

The control shows the worst of the three and expands to the three lines. `UNKNOWN` is neutral,
never green. The screenshot's "Flow Collectors 3/3" and "Event Processors 4/4" **have no data
source** (no endpoint, no model) and are **not shown**; they are a gap, not a placeholder.

**User menu.** Not built. No identity endpoint exists; the auth provider is `501` under OIDC. A
profile chip would be invented. Proposal B-1.3: `GET /me` returning `{subject, email,
permissions}` — needed anyway for UR.5.6 (permission-aware catalog) and for dashboard ownership
(UR.5.5). The chip is added after it exists and after OIDC lands (UR.10 Q3).

---

## UR.4 Home

The Home is `/`, built from the widget catalog (UR.5). Phase 2 builds the **default layout as
fixed components**; Phase 3 turns the same components into catalog widgets, so no widget is written
twice. The screenshot's composition, with the real data behind each part:

```
KPI row (4):  Total EPS | Log sources | Offenses | Anomalies
EPS chart (8 cols)                                | EPS by source class (4 cols)
Top sources (4)      | Anomaly severity (4)       | Geography (4)  <- blocked on geo
Recent anomalies (6)                              | Recent offenses (6)
```

### UR.4.1 Common rules for every widget

- **Titles say what and for what window**: "Total EPS, 24 h". Units on every number
  ("EPS", "events", "%"). Tabular figures, right-aligned numbers, times in UTC with the zone.
- **A drill-down** from every widget to the page that owns the data, with the filter pre-applied
  where the target supports it.
- **No relative-only times** (C4): "13:55 UTC" with the age in muted text ("12 min ago"), and a
  `<time datetime>` element.
- **No delta without a baseline.** The screenshot's "↑ 12 %" needs a previous period that the API
  does not give. It is dropped, or replaced by a deviation from expected where one exists.
- **The state set** (UR.5.4) applies to each. Below, "states" lists only what is specific.

### UR.4.2 KPI row

The screenshot has four cards. They are an allowed exception to "no row of identical KPI cards"
**only if** each card (a) is a different structure, (b) carries a breakdown, (c) drills down, and
(d) holds no vanity number (C1, ruling proposed). Guidelines §2 and §3 are kept for `/behavior`.

**1. Total EPS.**
- Value: sum of `mean_eps` over the classes returned by `GET /fleet/summary?window=24h` whose
  `mean_eps` is not `null` (`FleetClassSummary.mean_eps`, complete buckets only). It is a **mean
  over the window**, labelled as such.
- Beside it: "expected" = the sum of `expected_eps` over classes with `baseline_sources > 0`, with
  the coverage written out ("baselines cover 41 of 59 sources", from `baseline_sources` against
  `monitored_sources`). If coverage is below 100 %, the comparison says it compares only those
  classes. No percentage is shown when expected is `null`.
- Breakdown: the top class by EPS and its share (text).
- Drill-down: `/fleet`.
- States: `INSUFFICIENT_DATA` when every class `mean_eps` is `null`; `PARTIAL` when any class has
  `buckets.partial + buckets.missing > 0` (shows "n of m intervals collected").
- The sparkline is **omitted in Phase 2** (no series exists). It returns when B-2.1 lands, with a
  neutral line (never green; a gradient fill is not used, C9).
- Gap: no previous-window figure.

**2. Log sources.**
- Value: `monitored_sources` out of `total_log_sources` (`GET /anomalies/summary` →
  `monitored_sources`; `GET /overview` → `log_sources.total_log_sources`).
- Breakdown (the donut's replacement is the existing stacked bar `HealthDistribution`, which is
  already text-backed): from `GET /behavior/sources` states: Attention required (`OPEN`,
  `CANDIDATE`), Recovering, **Insufficient data**, Suppressed, Normal — the groups
  `healthDistribution()` already produces. `INSUFFICIENT_DATA` is neutral, never green.
- **Not** "97.4 % healthy": that number would count unbaselined sources as healthy. The
  card states "n of m judged normal", where "judged" excludes `INSUFFICIENT_DATA`.
- Silent sources: `summary.silent_sources` (anomaly-based `NO_EVENTS`), **not**
  `overview.silent_log_sources` (a `last_event_time IS NULL` approximation, UR.0.3 item 12). The
  card names the definition.
- Drill-down: `/behavior/sources`.
- Dependency: `GET /behavior/sources` is an N+1 read (task B-2.2).

**3. Offenses.**
- Value: open offenses. Source: `GET /offenses/analytics` → `aggregates.active` (and
  `critical`, `unassigned`, `exceeding_sla`), `read:offenses`. Not `/overview`.
- Breakdown: `magnitude_distribution` (`CountEntry[]`: key, count), grouped by the product's own
  threshold `aggregates.critical_magnitude` (default 7), reading "critical (magnitude ≥ 7)",
  "below". The screenshot's High/Medium/Low is **not** used: no such field exists, the severity
  bands would be invented.
- Drill-down: `/offenses?status=OPEN`.
- Precondition (UR.0.4): read `aggregate_metrics` for the no-snapshot case. If it returns zeros
  with no snapshot, the card must call `GET /offenses?limit=1` (`total`, and `captured_at`) to tell
  "no offenses" from "no data yet", and show `NOT_COLLECTED` for the latter.
- States: `NOT_COLLECTED` (no snapshot), `INSUFFICIENT_PERMISSION`.

**4. Anomalies.**
- Value: active anomalies: `summary.open_anomalies` (`GET /anomalies/summary`).
- Breakdown by **type** (data exists): spikes, drops, silent (`summary.spikes`, `drops`,
  `silent_sources`), and candidates/recovering. The screenshot's breakdown by severity needs one
  more call: `GET /anomalies?active_only=true&limit=200` and a count by `severity`; if
  `total > 200` the breakdown is labelled **TRUNCATED** ("first 200 of {total}") rather than shown
  as complete.
- Drill-down: `/anomalies?active_only=true`.
- Honest companion: the `insufficient_data_sources` count appears on the card ("n sources not
  judged"), per guidelines §3.
- Evidence backlog (`evidence_pending`, `evidence_failed`) moves to its own small widget, not this
  card.

### UR.4.3 The charts and tables

**Observed vs expected EPS** (headline chart).
- Data today (per source): `GET /behavior/sources/{id}/metrics?since&until&limit` (buckets with
  `completeness`), `GET /behavior/sources/{id}/baselines?metric=average_eps` (`median`, `p05`,
  `p95`, `is_reliable`), `GET /anomalies?log_source_id={id}` for markers. This is exactly what
  `VolumeChart` already draws; Phase 2 reuses it (`buildSeries`, `unobservedIntervals`).
- **Gap:** no fleet-wide series (UR.0.3 item 13). A fleet total of observed EPS needs a new bulk
  endpoint. Proposal B-2.1: `GET /fleet/eps-series?window=1h|24h|7d&group_by=none|class` returning
  per bucket `{bucket_start, events, eps, sources_complete, sources_expected}` from one
  `GROUP BY bucket_start` over the stored buckets (constant SQL; no per-source loop; no QRadar
  search). **Fail-closed rule:** a bucket is plotted only if every monitored source in scope has a
  `COMPLETE` bucket there; otherwise it is `null` with the coverage in the tooltip, and a shaded
  gap. A fleet sum over a bucket where sources are missing would understate and read as a drop.
- **The expected band at fleet level does not exist.** The sum of per-source medians is not the
  median of the sum, and a sum of `p05`/`p95` is a different, much wider band. Proposal: the
  fleet chart draws **observed** and, where every contributing source has a reliable cell, a single
  dashed "sum of per-source expected" line **without a band**, labelled as that. **Never invent
  confidence** (suggestion §4).
- Default content until B-2.1 lands: the chart shows **one source**: the source of
  `summary.highest_deviation[0]` if any, else the highest `observed_eps` of `GET /behavior/sources`.
  The widget's title names the source. Config: source picker. Interactive time selection: the
  range presets only (an ECharts zoom is a later enhancement).
- Anomaly markers: from `GET /anomalies` (`anomaly_start`, `anomaly_end`, `severity`), each marker
  with the words in its tooltip; markers are `crit`-tone because they are anomalies.
- Drill-down: click a marker → `/anomalies/{id}`; the title → `/behavior/sources/{id}`.
- States: `INSUFFICIENT_DATA` (no reliable baseline: observed only, no band, and the card says
  so), `PARTIAL`/missing intervals as shaded gaps (never connected), `NOT_COLLECTED`.

**Top log sources by EPS.**
- Data: `GET /fleet/summary?window=24h` → each class's `top_sources` (`{id, name, events}`, at most 5
  per class, `TOP_SOURCES = 5` in `app/fleet/summary.py`). The global top five is exactly the top
  five of the union, because a global top-five source is in its class's top five. EPS =
  `events` ÷ window seconds, over complete buckets only (the fleet definition).
- The screenshot's "Δ (24h)" has no data. Replace it with **deviation from baseline**
  (`deviation_ratio`) joined from `GET /behavior/sources` for the five ids, where `expected_eps` is
  not `null`; an em dash otherwise.
- Status column: the lifecycle state word (`NORMAL`, `OPEN`, `INSUFFICIENT_DATA`, …) as text with
  its tone, not a bare dot.
- Why not `observed_eps` from `/behavior/sources` for ranking: it is the latest bucket whatever its
  completeness (UR.0.3 item 11). Proposal B-2.2 adds `last_bucket_completeness` additively.
- Drill-down: row → `/behavior/sources/{id}`; "View all" → `/behavior/sources`.

**EPS by source class** (replaces "Events by Category", D-10).
- Data: `GET /fleet/summary?window=` → `classes[].mean_eps`. Sort descending; **top 4 classes plus
  "Other"** (the sum of the rest); `null` classes are left out and counted ("3 classes with no
  complete bucket"), never added as zero. `UNCLASSIFIED` is its own entry, named, not folded into
  "Other" silently: if it is not in the top four it joins "Other" and the legend says what "Other"
  contains.
- Draw: a donut with at most five slices (§3a), `--chart-cat-1…4` and `--chart-other`, a 2 px
  canvas gap between slices, the total EPS in the centre, and a **list beside it** with class,
  EPS and share (the text carrier; the donut is `aria-hidden`).
- Drill-down: slice or row → `/fleet/sources?class={class}`.
- States: `INSUFFICIENT_DATA` when no class has a mean; `PARTIAL` coverage line.

**Anomaly severity distribution.**
- Data: `GET /anomalies?since&until&limit=200` (`detected_at`, `severity`), counted per bucket
  client-side (hour buckets for 24 h).
- Draw: stacked columns with `--sev-*`, a legend with the words, an axis in "anomalies per hour".
- Honesty: if `total > 200` the widget is **TRUNCATED** and says so; it does not draw a partial
  stack as the whole. Proposal B-2.3 (optional): an aggregate endpoint `GET
  /anomalies/histogram` so the cap disappears.
- Drill-down: a column → `/anomalies?since&until&severity`.

**Log source health** (the screenshot places it inside a KPI card; as a widget it is
`HealthDistribution` + the counts).
- Categories, **only those with real detection logic**:

| Category | Source | Real? |
|---|---|---|
| Normal / Attention / Recovering / Insufficient data / Suppressed | `GET /behavior/sources` `state` via `healthDistribution()` | yes |
| Silent | `summary.silent_sources` | yes (`NO_EVENTS`) |
| Exceeding expected volume | `summary.spikes` | yes |
| Newly discovered | `GET /fleet/summary` → `new_sources` | yes |
| Conflicting class / unclassified | `conflict_sources`, `unclassified_sources` | yes |
| In maintenance | `overview.log_sources.in_maintenance` | yes |
| **Degraded** | none | **no such detection**; not shown |

**Recent anomalies.**
- Data: `GET /anomalies?active_only=true&limit=5` (`read:anomalies`; `subject_kind` unset, so WAF
  anomalies appear, with the endpoint named from `waf_endpoint`). Columns: detected time (UTC), source
  (`log_source_name`), type (`detectorLabel`), severity pill, state, deviation (`deviation_ratio`,
  em dash when `null`), "Investigate" → `/anomalies/{id}`.
- Badge "18 new" in the screenshot: **"new" has no definition** here. The badge shows `total`
  active ("18 active"), from the response's `total`.
- States: empty ("No active anomalies. Sources with no baseline are not being judged: n"), never
  "all clear".

**Recent offenses.**
- Data: `GET /offenses?status=OPEN&sort=last_updated_time&limit=5` (`read:offenses`). Columns: last
  updated (UTC), `description`, magnitude (a 10-segment bar **and** the number; `null` is an em dash,
  no bar), status. The segment bar is drawn with SVG (`HealthDistribution` precedent), not inline
  styles.
- Drill-down: row → `/offenses/{qradar_offense_id}`; "View all" → `/offenses`.
- States: `NOT_COLLECTED` when no snapshot exists (see the KPI precondition).

**Event sources by geography** — **blocked**. No geographic aggregate is collected. The widget
exists in the catalog as `status: "blocked"` (`NOT_COLLECTED`: "Geography is not collected yet")
and is **absent from the default layout until the geo collector is merged** (a template version
bump adds it). UR.6.

**Two more widgets the suggestion lists** and which have data:
- Detection health: `GET /coverage/summary` (`covered_ratio`, `by_status`),
  `GET /rules/health-summary`.
- Collection health: `GET /admin/search-cadence` (`last_run`, `next_dispatch_at`; `read:search_cadence`).

### UR.4.4 States common to every widget

Defined once in `WidgetFrame` (UR.5.4). A table of how each real data case maps:

| Case | State | Words (shape, not final copy) |
|---|---|---|
| Request in flight, nothing yet | loading | "Loading {what}" (plain text, polite live region, no spinner, as `LoadingState`) |
| Response with no rows | empty | what is empty and why ("No open offenses in the latest snapshot") |
| Value `null` | no data | an em dash with the reason beside it, never `0` |
| Source not collected | `NOT_COLLECTED` | "Not collected: {why}" |
| Collected but not judged | `INSUFFICIENT_DATA` | neutral tone, never green |
| Incomplete window | `PARTIAL` | "n of m intervals collected" |
| Capped list | `TRUNCATED` | "First 200 of {total}" |
| Data older than 2 cadences | stale | "Latest data {time} UTC, {age} old (cadence 1 h)" |
| `5xx`, network | error | what failed and what to do, never the exception text |
| `403` | insufficient permission | "Your role cannot read {what} (permission {perm}). Nothing was changed." |

---

## UR.5 Widget system (D-10: the full system now)

### UR.5.1 Registry and catalog

A widget is a typed entry in one registry (`frontend/src/widgets/registry.ts`):

```ts
interface WidgetDef<C> {
  type: string;                 // "kpi.total_eps": stable, ^[a-z0-9_.]{1,64}$, never renamed
  title: string; description: string;
  category: "KPI" | "Chart" | "Table" | "Health" | "Admin";
  requires: string[];           // permissions, e.g. ["read:fleet"]
  sources: string[];            // the exact API routes it reads (documentation + a test)
  status: "live" | "blocked";   // blocked: catalog lists it, cannot be added (NOT_COLLECTED)
  blockedBy?: string;           // "geo collection", "IPS view (T-052)"
  size: { w: number; h: number; minW: number; minH: number; maxW?: number; maxH?: number };
  range: null | Array<"1h" | "24h" | "7d">;   // per-widget override presets
  config: ConfigSchema<C>;      // enumerated options only; no free text, no AQL
  defaultConfig: C;
  drillDown: (cfg: C) => string | null;
  Component: React.ComponentType<WidgetProps<C>>;   // loaded with next/dynamic, ssr: false
}
```

Rules:
- **Config is enumerated.** Pickers over ids and enums, never a text field that reaches the
  server as a query. `AGENTS.md` rule 4: only stored, validated, versioned queries run, and the
  dashboard API **executes nothing** from a layout.
- **A registry test** asserts every widget's `sources` exist in `api.ts`, every `requires` is a
  known permission string, and no widget has `status: "live"` while its data is a gap.
- **The catalog** is a drawer listing widgets by category with the data each reads, the
  permission it needs, and the widgets that are `blocked` shown disabled with the reason. It lists
  only what exists; it does not tease.
- **Initial catalog** (every one with real data unless marked):

| Type | Data | Permission | Status |
|---|---|---|---|
| `kpi.total_eps` | `/fleet/summary` | `read:fleet` | live |
| `kpi.log_sources` | `/anomalies/summary`, `/behavior/sources`, `/overview` | `read:anomalies`, `read:overview` | live |
| `kpi.offenses` | `/offenses/analytics` | `read:offenses` | live |
| `kpi.anomalies` | `/anomalies/summary`, `/anomalies` | `read:anomalies` | live |
| `chart.eps_observed_expected` | per-source metrics, baselines, anomalies | `read:anomalies` | live (source mode, built in UI-3.5); fleet mode is `chart.eps_fleet_total` |
| `chart.eps_by_class` | `/fleet/summary` | `read:fleet` | live |
| `chart.anomaly_severity` | `/anomalies` | `read:anomalies` | live (capped at 200, TRUNCATED) |
| `table.top_sources` | `/fleet/summary`, `/behavior/sources` | `read:fleet`, `read:anomalies` | live |
| `table.recent_anomalies` | `/anomalies` | `read:anomalies` | live |
| `table.recent_offenses` | `/offenses` | `read:offenses` | live |
| `health.log_sources` | `/behavior/sources`, `/anomalies/summary`, `/fleet/summary` | `read:anomalies`, `read:fleet` | live |
| `health.detection` | `/coverage/summary`, `/rules/health-summary` | `read:coverage`, `read:rules` | live |
| `health.collection` | `/admin/search-cadence` | `read:search_cadence` | live |
| `health.evidence` | `/anomalies/summary` (`evidence_pending/failed`) | `read:anomalies` | live |
| `table.catchall_senders` | `/fleet/catchall/senders?review_status=NEW` | `read:fleet` | live (built in UI-3.5) |
| `table.fleet_classes` | `/fleet/summary` | `read:fleet` | live |
| `table.outage_policy` | `/fleet/summary` (`outage_mode`, `outage_overrides`) | `admin:outage` | live (admin) |
| `waf.sources` | `/behavior/waf/sources` | `read:anomalies` | live |
| `waf.endpoint_traffic` | `/behavior/waf/endpoints/{id}/metrics` (+ `/baselines`) | `read:anomalies` | live; config: endpoint |
| `table.recent_waf_anomalies` | `/anomalies?subject_kind=WAF_ENDPOINT` | `read:anomalies` | live |
| `ips.source_anomalies` | none | — | **blocked** until the IPS analyst API (T-085) and its UI |
| `map.geography` | none | — | **blocked**: the widget is task G-4 (geo collection, G-2, is built) |
| `chart.eps_fleet_total` | `/fleet/eps-series` (B-2.1) | `read:fleet` | live |

### UR.5.2 `react-grid-layout`: version, React 19, SSR

**Proposed version: `react-grid-layout@2.2.4`, pinned exactly** (the repository pins every
dependency exactly).

How I verified, and what I could not:
- **Published facts** (`npm view`, 2026-10-14): `2.3.0` is the latest (published 2026-10-05, nine
  days ago); `2.2.4` was published 2026-07-29; `1.5.4` is the `legacy` tag (2026-07-29). I propose
  `2.2.4` rather than `2.3.0` for the repository's habit of using a release that has had a few
  weeks of use; `2.3.0` is the alternative if the orchestrator prefers current. The licence is MIT.
- **Peer dependencies:** `react >= 16.3.0`, `react-dom >= 16.3.0`: the range admits React 19.
  Its README states "`>= 2.0.0`: React 18+, TypeScript". It ships its own types.
  Runtime dependencies: `clsx`, `prop-types`, `fast-equals@^4`, `react-draggable@^4.4.6`
  (resolved `4.7.2`), `react-resizable@^3.2.0` (`3.2.0`), `resize-observer-polyfill`.
- **Tested here:** in a scratch directory outside the repository I installed `react@19.2.7`,
  `react-dom@19.2.7` (the repository's versions) and `react-grid-layout@2.2.4` and ran:
  - `renderToString` of a grid with `useContainerWidth({ measureBeforeMount: true })`: renders the
    empty container (18 characters), no error;
  - the same without `measureBeforeMount`: server output contains the grid items;
  - a client render with `react-dom/client` under jsdom 25 and `act`: two `.react-grid-item`
    elements, **zero** `console.error` calls (no `findDOMNode` removal error; v2 passes a
    `nodeRef`, and `react-draggable@4.7.2` uses its own `findDOMNode` helper, not
    `ReactDOM.findDOMNode`).
- **Not verified:** drag and resize behavior. jsdom has no layout, so a synthetic mousedown and
  mousemove did not move an item (the transform stayed `translate(10px,10px)`); that result says
  nothing about a browser. **Acceptance item 1 of task UI-3.2 is a drag, a resize and a
  keyboard-alternative check in headless Chrome.** If it fails, the fallback is `2.3.0`, then
  `1.5.4` with the `legacy` import.
- **Do not use** the v1 `WidthProvider` (the README says to avoid it); use `useContainerWidth`.

SSR and hydration:
- The grid depends on the container's pixel width, which the server cannot know. The dashboard
  grid is a **client-only island**: `next/dynamic` with `ssr: false`, rendered under a static
  skeleton that is **text only** (`LoadingState`, no shimmer) of the same height class. The page
  shell, `PageHeader` and the widget titles render on the server; widget bodies mount on the
  client.
- `measureBeforeMount: true`, so no first paint at a guessed width, no hydration mismatch.
- The grid's own CSS (`react-grid-layout/css/styles.css`, 120 lines) is **not** imported as is.
  The few rules it needs (position, transitions, resize-handle) are rewritten in a component CSS
  file using tokens, so it brings no literals and no animation (`transition: none` outside drag).
- `react-grid-layout` adds about 480 KB unpacked to `node_modules`; its client bundle cost is
  measured in UI-3.2 and reported, and it is loaded only on routes that show a dashboard.

### UR.5.3 Interactions

| Interaction | Behavior |
|---|---|
| Add | The catalog drawer; adds at the first free spot at the widget's default size. |
| Remove | A control on the widget in edit mode; confirm only if the widget has non-default config. |
| Drag, resize | Edit mode only (a "Customize" toggle in the page actions). The grid is read-only otherwise, so a stray drag cannot move anything. |
| Keyboard alternative | RGL drag is pointer-only. Edit mode adds, per widget, **Move** and **Resize** steppers (arrow buttons changing `x`, `y`, `w`, `h` by one cell) in the configure drawer. A pointer-only editor fails §10. |
| Configure | A drawer with the widget's enumerated options and its range override. |
| Per-widget time range | `1h / 24h / 7d` for widgets that declare `range`; otherwise it follows the dashboard default; the widget title shows the effective range. |
| Expand | A native `<dialog>` (modal), the same component at a larger size and a longer series; `Esc` closes; focus returns to the trigger. |
| Restore defaults | Re-applies the dashboard's `template_key` layout (the frontend's template at its current version) after a confirm. A dashboard with no template key has no "restore": it offers "Reset to empty". |
| Navigate | The title and the drill-down link of each widget. |

Responsive: the grid uses 12 columns when its **container** is at least 900 px wide, 6 columns
from 600 to 899 px, 1 column below. At 1024×768 the main column is about 760 px, so it is the
6-column layout. Only the 12-column layout is stored; the others are **derived** by a pure,
tested function (`deriveLayout(lg, cols)`). **Editing is available only at the 12-column width**;
below it the page shows the layout and a line, "Widen the window to customize". The requirement at
1024×768 is no overflow and a usable read view (guidelines §9), not an editor.

### UR.5.4 Widget states

`WidgetFrame` owns the chrome (title, range, expand, configure, drill-down link) and a typed state:

```ts
type WidgetState<T> =
  | { kind: "loading" }
  | { kind: "ok"; data: T; asOf: string | null }
  | { kind: "empty"; reason: string }
  | { kind: "no_data"; reason: string }
  | { kind: "not_collected"; reason: string }
  | { kind: "stale"; data: T; asOf: string; ageSeconds: number }
  | { kind: "error"; action: string }
  | { kind: "forbidden"; permission: string };
```

`INSUFFICIENT_DATA`, `PARTIAL` and `TRUNCATED` are **qualifiers** a widget attaches to an `ok`
state (they have data and a caveat), not separate kinds. `stale` is derived: `asOf` older than
twice the family's `cadence_seconds` (from `GET /admin/search-cadence` when readable, else a
default of 2 h). A widget never renders `ok` with data it knows to be absent. Every state has a
Vitest test, and the registry test asserts every widget handles all of them.

Data fetching: widgets use one small in-house hook, `useApiResource(key, fetcher, {refreshMs})`,
with a shared in-flight map keyed by the URL, so six widgets that need `/fleet/summary?window=24h`
send **one** request. No new dependency (no SWR or React Query): the need is one hook. Requests
abort on unmount; refresh is driven by the top bar's interval.

### UR.5.5 Persistence

**Proposed tables** (additive; `AGENTS.md` rule 7). Names and column types follow the existing
models (`UUIDPrimaryKeyMixin`, `TimestampMixin`, string enums with `CHECK`).

`dashboard`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `owner_subject` | `String(255)`, NOT NULL | The principal's `subject` (`local-dev` today; the OIDC subject later) |
| `name` | `String(80)`, NOT NULL | Unique per owner: `UNIQUE (owner_subject, name)` |
| `description` | `Text`, NULL | |
| `visibility` | `String(16)`, NOT NULL, default `PRIVATE` | `CHECK IN ('PRIVATE', 'SHARED')` |
| `template_key` | `String(32)`, NULL | The frontend template it was created from; informational |
| `template_version` | `Integer`, NULL | |
| `schema_version` | `Integer`, NOT NULL | The layout JSON version (below) |
| `layout` | `JSONB`, NOT NULL | Widgets and geometry |
| `revision` | `Integer`, NOT NULL, default 1 | Optimistic concurrency |
| `created_at`, `updated_at` | `DateTime(tz)` | |
| `updated_by` | `String(255)` | |

`user_dashboard_pref`

| Column | Type | Notes |
|---|---|---|
| `owner_subject` | `String(255)` PK | |
| `default_dashboard_id` | UUID FK → `dashboard.id`, `ON DELETE SET NULL`, NULL | |
| `updated_at` | `DateTime(tz)` | |

Index `ix_dashboard_owner (owner_subject, updated_at DESC)`. Templates are **not rows**: they live
in the frontend next to the registry (one source of truth with the widget types; a test asserts
every template widget is in the registry and `status: "live"`). The backend stores only what a
user saved. A user with no dashboards sees the SOC Overview template unsaved; the first edit saves
it as theirs.

**Layout JSON, version 1.** The backend validates the structure, not the widget meaning:

```json
{
  "v": 1,
  "range": "24h",
  "widgets": [
    { "id": "w_9f3a", "type": "kpi.total_eps",
      "x": 0, "y": 0, "w": 3, "h": 3,
      "range": null,
      "config": {} }
  ]
}
```

- `v` must equal `schema_version`. A newer client writing an unknown `v` is refused (`422`); an
  older stored `v` is upgraded on read by a pure function with a unit test per step.
- Bounds, enforced by the API: at most **24 widgets**; `x` 0–11, `w` 1–12, `x + w ≤ 12`, `y` ≥ 0,
  `h` 1–24; `id` `^w_[a-z0-9]{4,12}$` and unique; `type` `^[a-z0-9_.]{1,64}$`; `range` null or one
  of `1h|24h|7d`; `config` an object of at most **4 KB** serialized; the whole layout at most
  **64 KB**; no unknown top-level keys. An unknown `type` is **stored and rendered as "Unsupported
  widget"** (so an older client does not destroy a newer client's layout).
- The server never interprets `config` and **never runs anything from it** (rule 4). The existing
  redaction (`app.security.redaction`) is not needed on read; config holds ids and enums.

**Limits:** 20 dashboards per owner, 24 widgets per dashboard, 64 KB layout, name 80 characters.
Exceeding returns `409` with the limit named.

**Ownership and sharing.**
- A dashboard belongs to `owner_subject`. `PRIVATE` is visible to the owner only. `SHARED` is
  visible read-only to every principal with `read:dashboards`; only the owner (or `admin:*`) edits
  or deletes it. A viewer can **duplicate** a shared dashboard into their own.
- Under the current auth every dev caller is `local-dev`, so all dashboards share one owner until
  OIDC (UR.10 Q3). This is stated on the page when `subject` is `local-dev`? **No**: the browser
  cannot know it. It is stated in this plan and in the migration's docstring only.

**RBAC** (`app/security/rbac.py` constants, following the file's pattern):
- `PERM_DASHBOARD_READ = "read:dashboards"`: satisfied by `read:*`. Lists and reads.
- `PERM_DASHBOARD_WRITE = "dashboard:write"`: create, update, rename, duplicate, delete,
  set default. **Not** granted by `read:*` (a read-only role cannot save layouts), like
  `log_source:write`.
- `PERM_DASHBOARD_SHARE = "admin:dashboards"`: setting `visibility = SHARED`. Satisfied by `admin:*`.
- The router guard is `requires(PERM_DASHBOARD_READ)`; write handlers add `require_permission`
  in the handler, as `catchall.py` and `classification_rules.py` do.
- A principal without `dashboard:write` still sees the template and can **view and expand**, and
  per-session changes (a range override, a configure) are held in memory and are not saved.

**API** (prefix `/api/v1/dashboards`):

| Method and path | Purpose | Permission |
|---|---|---|
| `GET /dashboards` | The caller's dashboards and the shared ones: `id, name, owner_subject, visibility, template_key, updated_at, widget_count, is_default` | `read:dashboards` |
| `POST /dashboards` | Create (`name`, `layout`, optional `template_key`, `template_version`) | `dashboard:write` |
| `GET /dashboards/{id}` | One, with `layout` and `revision` | `read:dashboards` |
| `PUT /dashboards/{id}` | Replace `name`, `description`, `layout`; body carries `expected_revision`; `409` with the current revision on mismatch | `dashboard:write`, owner |
| `PATCH /dashboards/{id}` | Rename, describe, set `visibility` | `dashboard:write`, owner; `admin:dashboards` for `SHARED` |
| `DELETE /dashboards/{id}` | Delete (`204`); clears any default pointing at it | `dashboard:write`, owner |
| `POST /dashboards/{id}/duplicate` | Copy into the caller's, `PRIVATE`, new name "Copy of …" | `dashboard:write` |
| `PUT /dashboards/default` | Body `{dashboard_id: uuid | null}` | `dashboard:write` |
| `GET /me` | `{subject, email, permissions}` for the caller (shell and catalog); **a separate small task** (B-1.3) | any authenticated |

The two-writer case (two tabs): `PUT` carries `expected_revision`; on `409` the UI shows "This
dashboard changed since you opened it" with "Reload" and "Save as a copy". Nothing is overwritten
silently.

**Audit.** Every write calls `record_audit` (`app/repositories/audit.py`) with
`action = dashboard.create | .update | .rename | .delete | .duplicate | .share | .set_default`,
`object_type = "dashboard"`, `object_id`, and a `detail` of `{name, revision, widget_count}`.
**The layout body is not written to the audit log** (size, and nothing in it is an action).
Reads are not audited (as for other list endpoints).

**Migration.** One new revision, chained after the head at the time (today `0019`; after T-069
lands, `0020`): create the two tables, the unique and the owner index, the `CHECK`s. It is purely
additive, seeds nothing (templates are in the frontend), and has a symmetric `downgrade()`. Gate:
upgrade, downgrade, upgrade clean; `alembic check` shows no drift **at head**
(`test_migration_*` pattern after T-076: the drift check runs at `head`). New integration test
`test_migration_00NN_dashboards`, `test_dashboards_api` (RBAC matrix, bounds, revision conflict,
limits, duplicate, default clearing, audit rows).

### UR.5.6 What a widget does without its permission

- **The catalog** hides, or shows disabled with the permission named, any widget whose `requires`
  the caller lacks, **when `GET /me` is available**. Without `/me` (UR.10 Q4) the catalog lists
  everything and relies on the next rule.
- **A placed widget that gets `403`** keeps its place and its configuration and renders the
  `forbidden` state: "Your role cannot read {what} (permission {perm}). Nothing was changed." It
  shows no number, no stale cache, and no zero. A dashboard shared with a viewer who lacks a
  permission shows exactly this in those cells, and the others normally.
- **A `401`** shows the session-expired line used by `actionErrorMessage`.
- **Admin widgets** (`admin:outage`, `admin:waf`, `admin:ips`) are not in the default layout of
  any template except "QRadar Administration".

### UR.5.7 Templates

Each contains only `live` widgets (the registry test enforces it). A template is a versioned
object `{key, version, name, layout}` in `frontend/src/widgets/templates.ts`.

| Template | Widgets | Left out until data exists |
|---|---|---|
| **SOC Overview** (the Home default) | `kpi.total_eps`, `kpi.log_sources`, `kpi.offenses`, `kpi.anomalies`, `chart.eps_fleet_total`, `chart.eps_by_class`, `table.top_sources`, `chart.anomaly_severity`, `health.log_sources`, `table.recent_anomalies`, `table.recent_offenses` | `map.geography` (geo) |
| **QRadar Administration** | `health.collection`, `table.catchall_senders`, `table.fleet_classes`, `table.outage_policy`, `health.detection`, `health.evidence` | none |
| **Behavioral Analytics** | `kpi.anomalies`, `chart.eps_observed_expected`, `table.recent_anomalies`, `health.log_sources`, `health.evidence`, `chart.anomaly_severity` | |
| **Log Source Monitoring** | `health.log_sources`, `table.top_sources`, `chart.eps_by_class`, `table.fleet_classes`, `table.catchall_senders` | |
| **WAF / IPS Monitoring** | `waf.sources`, `waf.endpoint_traffic`, `table.recent_waf_anomalies` | `ips.source_anomalies` (no IPS read API; T-052 and after) |

The "WAF / IPS Monitoring" name is the user's; until the IPS read API exists the template is WAF
only and says so in its description. A template version bump adds a widget; existing user copies
are **not** changed (they own their layout); "Restore defaults" applies the new version.

---

## UR.6 Geo collection (D-10)

D-10: validate the AQL geo functions on the live lab QRadar first; then collect a country-level
aggregate under the search cadence; no raw IP is ever stored.

### UR.6.1 Validation is done: T-077

The live, read-only check ran: [`tasks/T-077.report.md`](tasks/T-077.report.md). This plan adds no
validation task. Facts the design rests on (lab QRadar 7.6):

- `GEO::LOOKUP(<ip>, 'country', <name>)` is accepted and **GeoIP data is loaded**. It returns a
  **JSON string**, for example
  `{"country":{"confidence":null,"name":"United States","iso_code":"US","geo_id":6252001}}`.
- **The third argument is ignored.** `'name'`, `'iso_code'`, the two-argument form and an invented
  name all return the same string. The collector must take `country.iso_code` out of the string
  itself, in AQL or in Python.
- **Private (RFC 1918), loopback, TEST-NET and unknown public addresses all return `{}`**, not
  `NULL`. `GEO::LOOKUP(...) IS NOT NULL` removes nothing. Only an `INCIDR` filter separates
  private from unknown-public.
- The built-in `sourcegeographiclocation` and `destinationgeographiclocation` columns return only
  `other`. They are useless for a map and are not used.
- The geo key **can share a `GROUP BY` with `starttime / 300000`**. One fleet-wide hour took about
  1.1 s, the same as the control search with no geo key. The lab's row count (4 rows, one value
  `{}`) is not representative: real traffic could reach about 250 countries × 12 buckets = 3,000
  rows against `LIMIT 5000`, so truncation must be watched.
- On the lab data the external party is in `sourceip` for PAN and FTD, but that is the
  generator's convention only. There is **no WAF answer**.

### UR.6.2 Questions still open

The design below holds for any answer to these; none blocks the migration or the provider method,
and each is a configuration or a flag, not a redesign.

1. **GeoIP data age, coverage and skew on the production console.** The lab proves the data is
   loaded, not that it is current. The map is not called accurate until the user (or a
   production read-only check) has seen a real public-source window.
2. **Production cost and row count.** The 1.1 s figure is the lab's. The cadence default (1 h)
   and the country cap (UR.6.3) are set from the first production hour.
3. **Which field is the external party, per source class** (`sourceip` or `destinationip`),
   especially WAF. The collector therefore supports **both** directions, each a separate stored
   series (`direction`), and a per-class choice can be added later without a migration.
4. **Events with no IP.** The share of events that carry neither address is unmeasured. It is
   reported as its own remainder, never folded into "unresolved".
5. **JSON extraction in AQL.** Whether AQL can extract `iso_code` from the string (not tested).
   If not, the collector groups by the **full string** (accepted, stable), then parses it in
   Python and sums per code.

### UR.6.3 The collector (design)

Reuses the Phase A, WAF, IPS and catch-all pattern; no new mechanism.

- **One keyed fleet-wide search per direction per cadence tick**, constant per run (rule 9), on
  the **direct REST provider only** (rule 2), read-only (rule 1). A new provider method
  (`get_geo_keyed`) selects **only** the geo expression, `starttime / <bucket_ms>` and
  `SUM(eventcount)`, grouped by the first two. **No IP address is selected**, so none leaves
  QRadar (rule 3 holds by construction, not by deletion).
- **Private versus unknown.** Because both return `{}`, the query does not try to tell them
  apart inside the geo key. Optionally a second constant query counts events whose address is in
  the private ranges with `INCIDR`, so the remainder can be split into "private or internal" and
  "public, unknown to GeoIP". Without it the remainder is one number, "unresolved", and says so.
- **Fold (pure function).** Parse each key: valid `iso_code` (two letters, checked against the
  map's ISO list) → that code; `{}` → unresolved; anything unparseable → `unparseable`, counted
  and logged as a defect count (never a country). Sum per code.
- **Fail closed (rule 8).**
  - A window where **every** key is `{}` is **not** "no foreign traffic". It is stored as
    `INSUFFICIENT_DATA` (GeoIP not resolving anything), and the widget says "No address in this
    window could be located".
  - A failed, truncated or unsearched bucket gets **no row** (unknown, not zero; the catch-all
    convention); the watermark holds at the last measured bucket.
  - A result that reaches the `LIMIT` is `LOWER_BOUND`, and the API carries it.
- **Family and cadence.** A new `SearchFamily.GEO_COLLECTION` at the **end** of the enum (after
  `CATCHALL_SENDERS`), a `search_cadence` row seeded by the migration (default 1 h, like every
  family), dispatched through the existing cadence gate.
- **Persistence (aggregate only).** `geo_country_bucket(bucket_start, bucket_seconds, direction,
  country_code CHAR(2) NULL, events BIGINT, measurement, collected_at)`. A `NULL` code is a
  remainder row, one per kind (`UNRESOLVED`, `OVER_CAP`, `NO_ADDRESS` when measured), each with its
  event count. At most 64 countries per bucket; the rest go to `OVER_CAP`. Retention follows
  `catchall_retention`. The raw JSON string is not stored.
- **API.** `GET /geo/countries?since&until&direction` → per country `{country_code, events,
  share}`, plus the remainders, `measurement`, and coverage (`measured_buckets`,
  `expected_buckets`). Permission: a new `read:geo` (satisfied by `read:*`) or `read:fleet`,
  decided at spec time. No country filter is added to the anomaly API; the map does not claim a
  drill-down that does not exist.
- **UI.** The `map.geography` widget: vendored Natural Earth 1:110 m GeoJSON (public domain, about
  100 to 250 KB, in `frontend/src/assets/`, no network), the ramp of UR.2.7, a numeric legend, a
  table beside it (country, events, share), the remainders and coverage as text, zoom controls.
  States: `NOT_COLLECTED` (no row yet), `INSUFFICIENT_DATA` (all `{}`), `PARTIAL` coverage,
  `LOWER_BOUND`.

### UR.6.4 Tasks

| ID | Task | Type | Notes |
|---|---|---|---|
| T-077 | Live read-only AQL geo validation | live, scratch | **Done** (report above) |
| G-1 | Geo design addendum: exact AQL, caps, remainders, permission name | orchestrator, doc | Closes questions 3 and 5 of UR.6.2 as far as the lab can |
| G-2 | Provider method, fold, model, migration, collector, family, tests (fold: `{}`, valid, unparseable, all-`{}` window, truncation) | backend | Pattern: T-069 |
| G-3 | `GET /geo/countries`, RBAC, API tests | backend | |
| G-4 | `map.geography` widget, vendored GeoJSON, states, screenshots | UI | After Phase 3 |
| G-5 | First production read-only window; closes questions 1 and 2 | user or orchestrator | Gates the word "accurate" |

---

## UR.7 Phases and task split

**Conventions.**
- **UI** tasks are Sonnet or Codex only (the orchestrator's rule). **Backend** tasks may use any
  agent.
- Every UI task names its allowed files; touching another is a stop-and-report (`AGENTS.md` §1).
- Every UI task ends with the §3a gates: `npm run typecheck`, `npm run lint`, `npm test`, and
  **screenshots at 1440×900, 1280×800 and 1024×768** of the routes it changes, against a
  deterministic fixture API (`docs/UI-WORKSTREAM-COMPLETION.md` §4), the capture script **outside
  the repository**, PNG paths listed in the report. Overflow is measured with
  `document.documentElement.scrollWidth === clientWidth`.
- Test edits are the exception, listed by file and assertion in the task. "Never weaken a test"
  stands: each edit keeps the invariant and changes only the value it pins.
- **Order of landing before Phase 1:** T-070b merges first (its CSS and `nav.ts` entry must be on
  the base, UR.8.7).
- **Worktrees:** one worktree per UI task, branched from the previous accepted phase.

### Phase 1 — Foundation (tokens, typography, shell)

| ID | Task | Allowed files | Acceptance | Screens |
|---|---|---|---|---|
| UI-1.1 (UI) | Charcoal tokens | `app/tokens.css`; `lib/chartPalette.ts` (+ test); new `lib/contrast.test.ts`; `app/shellFixes.test.ts` (one assertion: `.layout::before` background becomes `var(--sidebar-bg)`) | Every token of UR.2.1 and UR.2.3 present; the contrast test asserts the UR.2.8 thresholds; `chartPalette.test.ts` unchanged and green; no literal outside `tokens.css`; `globals.css` still has zero colour literals | **All 32 routes** at the 3 viewports (colour pass; they must look intact, not redesigned) |
| UI-1.2 (UI) | Inter, self-hosted | `app/fonts/*` (2 woff2 + `OFL.txt`), `app/fonts.css`, `app/globals.css` (one `@import`), `app/tokens.css` (`--font`) | No request to any external host (verified by the headless run's network log); Turkish characters render in Inter; fallback stack documented; build image still builds | `/behavior/sources/[id]`, `/anomalies/[id]`, `/fleet` at 3 viewports |
| UI-1.3 (UI) | Shell: layout, sidebar, groups, selection style | `app/layout.tsx`, `components/shell/AppNav.tsx`, `lib/nav.ts`, `app/globals.css` (shell section), `lib/nav.test.ts` and `components/shell/AppNav.test.tsx` (edits listed below) | UR.3.1 and UR.3.2 exactly; one `aria-current`; no heading in the shell; all routes reachable; no overflow at 1024 | Every route at 3 viewports (the shell is on all of them) |
| UI-1.4 (UI) | Shared components restyle: buttons, inputs, pills, tables, notices, banners, states | `app/globals.css`, `components/ui/controls.css`, `components/ui/*.tsx` (markup only if a class needs adding; no class a test pins is renamed) | Primary button neutral; input edge `--border-strong`; severity pills as UR.2.4; `RouteState` unchanged in contract; all existing tests green with **no** edits | Fleet, anomalies, behavior, admin routes at 3 viewports |
| UI-1.5 (UI) | `TopBar` | new `components/shell/TopBar.tsx`, `TopSearch.tsx`, `AutoRefresh.tsx`, `AlertBell.tsx`, `ConnectionStatus.tsx` (+ tests), `app/layout.tsx` (mount), `app/globals.css` | Each element of UR.3.4; every state tested: search loading, empty, partial permission (`403` group omitted with the footer line), bell unavailable (never `0`), status worst-of-three, refresh pauses on hidden tab; no QRadar call | Dashboard and two others, with every top-bar state forced from the fixture |
| B-1.1 (backend, optional) | `q` on `GET /anomalies` | `app/api/routes/anomalies.py`, repository, tests | Case-insensitive substring of the source name, LIKE wildcards literal (the `fleet/sources` pattern) | — |
| B-1.2 (backend) | `instance_last_health_check_at` on `SocOverview` | `app/schemas/log_source.py`, `app/services/overview.py`, tests | Additive; null when never checked | — |
| B-1.3 (backend) | `GET /me` | `app/api/routes/`, `router.py`, `schemas`, tests | Returns `subject`, `email`, `permissions` of the caller; no secret | — |

UI-1.3 test edits (the only ones): in `nav.test.ts`, the three `NAV` structure assertions are
rewritten to the groups of UR.3.2, **with the same intents**: the first group is unlabeled and
leads; behavioral destinations are grouped first among labeled groups; the administration list
equals today's five; "links to no route that does not exist" is unchanged and adds `/reports`. In
`AppNav.test.tsx`, only an assertion that names a group label the regroup removes changes.

### Phase 2 — Home

| ID | Task | Type | Allowed files | Acceptance |
|---|---|---|---|---|
| B-2.1 | `GET /fleet/eps-series` | backend | `app/api/routes/fleet.py`, `app/fleet/series.py`, `schemas/fleet.py`, tests | One SQL per request regardless of source count; a bucket is `null` unless every monitored in-scope source has a `COMPLETE` bucket; coverage counts returned; `group_by=class`; range bounded by `api_max_range_days`; no QRadar call |
| B-2.2 | Bulk `/behavior/sources` and `/anomalies/summary` | backend | `app/api/routes/anomalies.py`, `app/repositories/anomaly.py`, tests | Query count is constant in the number of sources (asserted); additive `last_bucket_completeness`; response otherwise identical (the existing API tests pass unchanged) |
| B-2.3 (optional) | `GET /anomalies/histogram` | backend | `app/api/routes/anomalies.py`, repo, tests | Counts by severity per hour for a range; removes the 200-row cap |
| UI-2.1 | Home data layer | UI | `lib/home/*.ts` (+ tests), `hooks/useApiResource.ts` (+ test) | Pure derivations (total EPS, top 4 + Other, severity counts, top sources) with null-not-zero tests; shared in-flight dedupe test |
| UI-2.2 | KPI row (4 cards) | UI | `components/home/Kpi*.tsx` (+ tests), `globals.css` or a new `home.css` | UR.4.2 card by card; every state; no sparkline until B-2.1; a drill-down link on each |
| UI-2.3 | Charts | UI | `components/home/EpsChart.tsx`, `EpsByClass.tsx`, `SeverityChart.tsx` (+ tests) | Reuses `VolumeChart` internals for the EPS chart; donut at most 5 slices with list; honesty tests as `VolumeChart.test.tsx`; tokens only |
| UI-2.4 | Tables and health | UI | `components/home/TopSources.tsx`, `RecentAnomalies.tsx`, `RecentOffenses.tsx`, `LogSourceHealth.tsx` (+ tests) | UR.4.3; em dash for null; magnitude bar plus number; UTC with zone |
| UI-2.5 | `/` composition | UI | `app/page.tsx`, `app/loading.tsx`, `app/error.tsx`, `app/page.test.tsx` | The screenshot's composition at 12 columns, fixed positions; 1024 reflows without overflow; first screen answers "what needs me now"; page test covers hierarchy and each state |

Screens (every Phase 2 UI task, final one required): `/` at the 3 viewports with **ok**, **empty**,
**partial permission**, **backend down** and **insufficient-data** fixtures.

### Phase 3 — Widget system

| ID | Task | Allowed files | Acceptance | Screens |
|---|---|---|---|---|
| UI-3.1 (UI) | Registry, `WidgetFrame`, state union, catalog drawer, `deriveLayout`, Home as `DashboardView` over the template (in memory) | `src/widgets/*` (+ tests), `app/page.tsx` (+ tests) | Registry test (sources exist, permissions known, no live widget on a data gap); every `WidgetState` kind rendered and tested; Phase 2 components wrapped, not rewritten; 12/6/1 column derivation tested; same visual result as Phase 2; add and remove work in the session, **nothing is saved** | Catalog drawer, one of each state, `/` default and customized |
| UI-3.2 (UI) | `react-grid-layout@2.2.4` | `package.json` (+ lockfile), `src/widgets/Grid.tsx`, `grid.css` (+ tests) | **Drag, resize and the keyboard stepper verified in headless Chrome**; SSR guard (`ssr: false`, `measureBeforeMount`); client bundle cost measured and reported; no animation outside drag | Edit mode at 1440 and 1280; read mode at 1024 |
| UI-3.3 (UI) | Configure drawer, expand dialog, per-widget range | `src/widgets/*` | Config enumerated only; `<dialog>` focus return; range label on the title | Each of the three, at 3 viewports |
| UI-3.5 (UI) | The remaining catalog widgets of UR.5.1 that UI-3.1 does not register | `src/widgets/*` (+ tests) | Each registered `live` with its real `sources` and `requires`; every state tested; no widget added on a data gap | Each new widget, 3 viewports |

The split was changed on 2026-10-14 (the orchestrator's ruling in
[`tasks/UI-3.1.md`](tasks/UI-3.1.md)): UI-3.1 and UI-3.4 became one task because neither can be
seen in a browser without the other, `deriveLayout` moved to UI-3.1 with them, and the catalog
widgets UI-3.1 does not register became UI-3.5.

### Phase 4 — Persistence

| ID | Task | Type | Allowed files | Acceptance |
|---|---|---|---|---|
| B-4.1 | Models and migration | backend | `app/models/dashboard.py`, `models/__init__.py`, `alembic/versions/00NN_dashboards.py`, `tests/integration/test_migration_00NN.py` | UR.5.5 columns; upgrade, downgrade, upgrade clean; `alembic check` no drift at head; single head |
| B-4.2 | Service, API, RBAC, audit | backend | `app/api/routes/dashboards.py`, `app/schemas/dashboard.py`, `app/security/rbac.py`, `router.py`, `app/services/dashboards.py`, tests | The routes and bounds of UR.5.5; RBAC matrix; revision conflict; limits; audit rows; the layout is never executed |
| UI-4.1 | Client API and dashboard management | UI | `lib/dashboardApi.ts`, `src/widgets/DashboardSwitcher.tsx`, `DashboardMenu.tsx` (+ tests) | Create, rename, duplicate, delete, set default, switch; `409` UX; `403` hides write controls |
| UI-4.2 | Save, restore, templates | UI | `src/widgets/templates.ts`, `useDashboard.ts` (+ tests) | Save on "Done"; restore defaults; the five templates; unsaved-changes guard; default dashboard loads on `/` |
| UI-4.3 | `/` loads the user's default | UI | `app/page.tsx` | No dashboard → SOC Overview template, unsaved |

Screens: `/` with the switcher open, the conflict dialog, the read-only state (no `dashboard:write`).

### Phase 5 — Every remaining page

Each task restyles; none changes behavior. Where a route has **no page test**, the task adds a
render test for each state **first** (characterization, committed before the restyle), because a
restyle with no test is unguarded.

| ID | Routes | Allowed files | Pre-work (tests) |
|---|---|---|---|
| UI-5.1 | `/behavior`, `/behavior/sources`, `/behavior/sources/[id]` | page files, `components/behavior/*`, `globals.css` | none: pinned |
| UI-5.2 | `/anomalies`, `/anomalies/[id]` | page files, `components/behavior/Incident*`, `Evidence*`, `Dimension*`, `Lifecycle*`, `Contributor*` | none: pinned |
| UI-5.3 | `/behavior/waf`, `/behavior/waf/sources/[id]` | `components/waf/*`, page files | none: pinned |
| UI-5.4 | `/fleet`, `/fleet/sources`, `/fleet/catchall` | `components/fleet/*`, `app/fleet/**` | none: pinned |
| UI-5.5 | `/offenses`, `/offenses/[id]`, `/rules`, `/rules/[id]`, `/rules/statistics`, `/coverage` | page files, `components/rules/*`, `OffenseFilters.tsx` | add render tests for `/offenses`, `/offenses/[id]`, `/coverage` |
| UI-5.6 | `/log-sources`, `/log-sources/[id]`, `/searches`, `/searches/[id]`, `/alerts`, `/alerts/[id]` | page files, `components/searches/*`, `AlertActions.tsx`, `ResultTrend.tsx`, `RunSearchButton.tsx` | add render tests for the six pages |
| UI-5.7 | `/admin`, `/admin/search-cadence`, `/admin/classification-rules`, `/admin/outage-policy`, `/admin/waf-sources`(+`[id]`), `/admin/ips-sources`(+`[id]`) | `app/admin/**`, `components/cadence/*`, `components/fieldmapping/*` | none: pinned; fixes the stale "Phase 4" hub copy |
| UI-5.8 | `/config-changes`, all `loading.tsx` and `error.tsx` | those files | add a test (none exists); no copy that says "Something went wrong" |

Screens: each task, its routes at 3 viewports. 32 routes × 3 = 96 PNGs for the phase.

### Phase 6 — QA

| ID | Task | Type | Notes |
|---|---|---|---|
| UI-6.1 | Full viewport matrix and overflow measurement | UI | All 32 routes, 3 viewports, the script outside the repo; zero page-level overflow |
| UI-6.2 | Accessibility pass | UI | Keyboard walk of the shell, search, catalog, edit mode, dialogs; focus order; names; the contrast test (UR.2.8) green; no claim of WCAG conformance unless a real audit is run |
| UI-6.3 | Performance | UI | A 24-widget dashboard in headless Chrome: time to first widget, long tasks, request count at idle and at refresh; thresholds in UR.8.3 |
| UI-6.4 | Cleanup | UI | Remove `echarts-for-react` (unused); delete `docs`-only dead CSS found by the pass; confirm `globals.css` still has zero colour literals |
| UI-6.5 | Docs | orchestrator | Update `PRODUCT-UX-GUIDELINES.md` and `AGENTS.md` §3a with the user-approved rulings (UR.9); `AGENTS.md` and `PLANNER.md` are not agent-editable |

### Parallel track — geo (UR.6.4)

Runs beside Phases 2–4 (T-077 is done); blocks only the `map.geography` widget and the SOC
Overview template's version 2.

---

## UR.8 Risks

### UR.8.1 Test churn from the restyle

About 1,460 test cases in 92 files. What the restyle can touch:

- **Class-name assertions** (about 171 lines of `className`/`toHaveClass`/`querySelector`): `num`,
  `crit`, `perm`, `nowrap`, `cell-name`, `cell-end`, `cell-fold`, `code-block`, `ips-status`,
  `is-warn`, `cron`, `sr-only`, `pill`, `health-seg`. **Rule: restyle in CSS, never rename a
  pinned class.** New classes may be added.
- **CSS contract tests** read `globals.css` and `tokens.css`: `shellFixes.test.ts` (sidebar
  painted on `.layout::before` using `var(--surface)`; `.grid-4` has `grid-auto-rows: 1fr`;
  `color-scheme: dark` in `tokens.css`; `td.num.cell-when` and `.pill.pill-wrap` fold rules),
  `TableScroll.test.tsx` (`.table-scroll` stays positioned), `chartPalette.test.ts` (token names;
  expected range not a status hue). **One needs an edit:** the sidebar token (UI-1.1). The
  `color-scheme: dark` assertion **stays** (UR.8.5).
- **Structure tests** pin hierarchy: `app/behavior/page.test.tsx` (four cards), source and
  incident page tests (fixed order, four metrics), `AppNav`/`nav` tests. The restyle keeps the DOM
  order; Phase 5 is CSS plus minimal markup.
- **Semantics tests** (`statusSemantics.test.ts`, `StatusBadge.test.tsx`): `sevTone`, tone words.
  Unchanged; the tone class names `ok`, `warn`, `crit`, `info` stay.
- **Intent preservation:** each test edit is listed in its task (UI-1.1, UI-1.3). Any other
  failing test is a stop-and-report, not an edit.
- **Routes with no test** (about 12) get characterization tests **before** their restyle
  (UI-5.5, 5.6, 5.8, 2.5).

### UR.8.2 `AGENTS.md` §3a and guidelines collisions

See UR.9: twelve collisions with proposed rulings. None is implemented before the orchestrator
rules; the user's approval of this plan covers the rulings the orchestrator accepts.

### UR.8.3 Performance with many widgets

- **Client requests.** Without dedupe, 11 default widgets make about 25 reads per refresh. The
  `useApiResource` shared in-flight map reduces the default Home to **about 9 distinct requests**
  (`/fleet/summary` ×1, `/anomalies/summary`, `/behavior/sources`, `/offenses/analytics`,
  `/anomalies` ×2, `/offenses`, `/overview`, one metrics + one baselines for the chart). At a 5-min
  refresh that is under 2 requests per minute, all against the application database.
- **Two N+1 endpoints** (UR.0.3 item 10) are the real cost. B-2.2 makes them constant before the
  Home ships; until then the default refresh is `Off` for any dashboard containing
  `kpi.log_sources`.
- **Rendering.** ECharts instances: at most 4 on the default Home; each is created on mount,
  disposed on unmount, and **off-screen charts are not created** (`IntersectionObserver`). The grid
  re-renders only on layout change (memoized children).
- **Bundle.** ECharts is imported modularly (about 340 kB saved against the full build, per
  `VolumeChart`). The map adds `MapChart`, `GeoComponent`, `VisualMapComponent` and the GeoJSON,
  loaded only when the widget is on the page (`next/dynamic`).
- **Budgets** (measured in UI-6.3, reported, not guessed here): first widget content under
  2 s on the lab VM for the default Home; at idle no network; no long task above 100 ms during
  drag. These are targets to measure against, not claims.
- **Limits** (24 widgets, 64 KB) bound the worst case.

### UR.8.4 SSR and hydration

- The grid is a client-only island (UR.5.2). Everything above it (shell, header) is server
  rendered. Widget bodies fetch in the browser, so the first server response is fast and shows
  text-only loading states; the cost is a flash of loading text, accepted over a hydration
  mismatch.
- `useContainerWidth({ measureBeforeMount: true })`; no `window` access during render.
- `readChartPalette()` already runs in an effect (browser only); it is re-read when a chart is
  created, not on a theme event (there is no theme switch, UR.8.5).
- The browser needs `NEXT_PUBLIC_API_BASE_URL` and the backend's CORS to accept it. Existing
  client mutations already depend on both. **Check at UI-2.1** that a browser `GET` (not only a
  mutation) works from the published origin, because widget reads become browser reads for the
  first time.
- Time displays that depend on "now" ("12 min ago") render **after mount only** (a server and a
  client clock differ and would mismatch).
- React 19 StrictMode (`reactStrictMode: true`) double-invokes effects in development: the hook
  must be idempotent and abort-safe. Tested.

### UR.8.6 The 1024×768 viewport

(Numbered 8.6 to keep the headings of the spec's list; 8.5 follows.)

- Main column is about 760 px (1024 − 232 sidebar − 2 × 32 padding). The Home gets the 6-column
  grid; the four KPI cards go two by two; the EPS chart takes the full width; tables scroll
  **inside** `TableScroll`, never the page.
- The top bar at 1024 px: the search shrinks (`minmax(0, 1fr)`), the status collapses to a
  coloured word + a button, the refresh select keeps its width (guidelines §9: intrinsic controls
  are not stretched).
- The edit mode is unavailable below 900 px of container width (UR.5.3).
- Verified at the three viewports in every task's screenshots and measured in UI-6.1.
- `overflow-x: hidden` on any container remains prohibited (guidelines §9).

### UR.8.5 Dark and light

**Is light mode kept? Proposal: there is none to keep, and none is added.** Today the product is
dark only (`color-scheme: dark` in `tokens.css`, asserted by `shellFixes.test.ts`; no
`prefers-color-scheme` rule exists). The suggestion and the user specify one charcoal theme, the
SOC context is dark, every screenshot is dark, and every chart reads its colours **once, at
creation** (`readChartPalette`), so a live theme switch would need a re-read protocol and a second
contrast table. The tokens are structured so that a `[data-theme="light"]` block could be added
later without touching `globals.css`. If the user wants light mode it is a separate, later
task; it is **not** in this plan's phases (UR.10 Q6 states the default).

### UR.8.7 Work in flight landing during the restyle

| In flight | State | Collision | Handling |
|---|---|---|---|
| **T-070a** `/admin/outage-policy` | Merged | Written against the navy tokens | Restyled in UI-5.7; its tests pin `num`, `cell-end` |
| **T-070b** `/fleet/catchall` | Finished, unmerged (`t070b`); also edits `globals.css` (+122 lines), `lib/nav.ts` (one entry), `lib/api.ts`, `searchCadence.ts` | `globals.css` and `nav.ts` are exactly the files Phase 1 rewrites | **Merge T-070b (and T-069/T-076 Part B ahead of it) before UI-1.1 starts.** Its CSS then rides the token change; UI-5.4 reviews it |
| **IPS analyst view** (T-052 and after, Phase D) | Planned, unbuilt; will add `/behavior/…` routes and components, and probably a nav entry | May land before, during or after Phase 5 | (a) If before: it is restyled in UI-5.x like the rest. (b) If during: it is built against the new tokens and components; the rule is already §3a (tokens only, existing components, no inline styles). (c) Its CSS goes in a **component-colocated stylesheet** (the `controls.css` precedent), **not** in `globals.css`, so it does not conflict with the restyle. (d) Its nav entry joins the Behavior group of UR.3.2. (e) It adds the `ips.source_anomalies` widget to the catalog and the "WAF / IPS Monitoring" template |
| **Geo track** | Planned | Adds a `SearchFamily` value, a migration, a map widget | Independent; touches `app/models/enums.py` and the cadence UI list; the Phase 4 migration chains after whichever lands first |
| **T-076 Part B** (migration re-chain) | In flight | Sets the head the dashboards migration chains after | Phase 4 reads the head when it starts |
| **T-051a** (IPS detectors, pure) | In flight | Backend only | None |

**General rule while the restyle runs:** every new UI task, from any workstream, uses tokens and
existing components only, adds no hex literal, and puts new CSS beside the component. That keeps
the restyle's blast radius at `tokens.css` and the shared stylesheet.

### UR.8.8 Other risks

- **The migration number** (UR.0.3 item 9). Numbers in UR.5.5 are placeholders.
- **Browser-side auth.** The browser sends no credentials; in production, every authenticated
  route fails until OIDC lands (PLANNER "Later"). The dashboard persistence can be built and tested
  with the dev principals only. State it in the acceptance report.
- **A shared `local-dev` owner.** Until OIDC, all dev users share dashboards. Documented, not
  hidden.
- **Search load.** Four reads per keystroke: debounce 250 ms, abort, cap 5 results. Application DB
  only.
- **Scope creep.** The widget system is large. Phases 2 and 3 are separated so Home ships even
  if persistence slips.

---

## UR.9 Collisions with `AGENTS.md` §3a and the guidelines, and proposed rulings

The orchestrator decides. "Request" means the user's own words or D-10.

| # | Collision | Source of the rule | Proposed ruling |
|---|---|---|---|
| C1 | The screenshot's **four KPI cards** | §3a "a row of identical KPI cards holding vanity numbers"; guidelines §2, §3 ("four counters, not ten"), §8 `StatCard` | Allowed **on `/` only**, and only as UR.4.2: four **different** structures, each with a breakdown from real fields, a drill-down, honest states, no vanity delta. `/behavior` keeps its pinned four counters. Record in §3a: "a KPI row is allowed when each card carries a breakdown and drills down." |
| C2 | The screenshot's **six-slice donut** ("Events by Category") | §3a "pie chart with more than five slices" | D-10 already replaces it: EPS by source class, top 4 + Other = **five** slices, with a list beside it (UR.4.3). |
| C3 | **Two overviews**: `/` (SOC Overview) and `/behavior`; the screenshot's "Behavioral Overview" mixes behavior with offenses at equal weight | Guidelines §1 ("supporting routes must not be given the visual weight … of the behavioral ones"), §3 (`/behavior` must not carry the inventory), the request ("follow the screenshot") | `/` becomes the Dashboard (a cross-capability home, the user's explicit request); `/behavior` stays the behavioral triage page with its pinned tests. Behavioral widgets get the larger area of the Dashboard. Offenses appear as one KPI and one table, as in the screenshot (the request wins on composition). Add to guidelines §3: a `/` entry describing the Dashboard. |
| C4 | The screenshot's **relative times** ("2 min ago", "15 min ago") | Guidelines §10 ("Timestamps are UTC and always carry the zone"); §3a "Times show their timezone" | Show the **absolute UTC time with the zone**; the age may follow in muted text. Never relative alone. `<time datetime>`. |
| C5 | The page subtitle "Real-time visibility into your QRadar environment…" and the brand tagline "Security Insights. Operational Clarity." | §3a "hero or marketing copy, or text that restates the heading"; guidelines `PageHeader` ("one line of orientation, not documentation") | Dropped. The description is a fact (window, data age). The tagline and the "v2.1.0" footer are dropped (no version source exists). |
| C6 | The **"Live"** indicator | §3a; `AGENTS.md` rule 8 (honesty); the 1 h collection cadence (SEARCH-CADENCE) | D-10 already: "auto-refresh with last-updated time". Plus the **data age** next to it, because a page refresh is not a fresher measurement. No word "live" or "real-time" anywhere. |
| C7 | **Status colours as decoration**: the donut uses green/red/orange as categories; the map legend is Low-green, Medium-amber, High-red; the sparklines are green; KPI icon tiles are red- and amber-tinted | `tokens.css` rule 1 ("status colour is reserved for status"); guidelines §10 ("a severity never takes the positive tone: `LOW` is `info`, not `ok`"); §3a "no decorative icons" | Categorical charts use `--chart-cat-*` (none of green, amber, red); the map uses the one-hue ramp `--map-*`; sparklines are neutral; no tinted icon tiles. |
| C8 | **Icons**: the sidebar glyphs, KPI icons, and source-type brand icons (the Windows, firewall and Office logos in the tables) | §3a "emoji or decorative icons" | No icons in the nav, KPI cards or tables. Source type is its **name**. (UR.10 Q2 asks whether a small set of functional glyphs is acceptable in the nav.) |
| C9 | **Global time range** in the top bar | Guidelines §3 (each route has one job), the pinned page-local selectors (`RangeSelector`, `WindowSwitch`, `WafRangeSelector`) | The top-bar range is the **default for dashboard widgets only** and appears only on routes that register support (UR.3.4). It does not rewrite other pages. |
| C10 | **Gradient fills** under the sparklines and the EPS area; glow-like soft shadows | §3a "gradients, glows, glassmorphism, decorative shadows" | Solid fills with alpha only (`--chart-band`); one shadow token. |
| C11 | **Muted text `#73757B` and the border `#35363A`** from the binding palette | WCAG AA (4.5:1 text, 3:1 non-text); `tokens.css` (text-3 "is already the floor at which 12 px stays legible") | UR.2.2: `--text-3 = #8C8E94`, `#73757B` becomes `--text-disabled`; `--border-strong = #6E7077` for input edges; `--border` stays `#35363A` as a hairline. This **changes two binding hex values**; the user should confirm (UR.10 Q7). **Superseded by D-11 (2026-10-14): the user kept both palette values — `--text-3 = #73757B`, `--border-strong = #35363A` — accepting that `#73757B` is below AA; see UR.2.2.** |
| C12 | **Blue as the data accent**: today `--info` blue is the observed line, the link, the primary button and the focus ring | Suggestion ("avoid blue as the dominant visual identity"; "neutral gray for … standard buttons") | `--info` becomes cyan `#6FD0E0`, used for the observed series, links and the `LOW` pill; the primary button is neutral light; the focus ring is neutral. The word "info" and the class names are unchanged. |
| C13 | **Severity hues**: the screenshot distinguishes Critical (red) from High (orange) | `sevTone()` pinned: `CRITICAL` and `HIGH` both `crit` (guidelines §10); no orange token | Pills keep one `crit` tone and the word. Charts use `--sev-critical` and a darker `--sev-high`, each labelled. No orange. |
| C14 | **A Top-N "Δ (24h)"**, **"97.4 % healthy"**, **"↑ 12 %"** | `AGENTS.md` rule 8, §3a ("placeholder, random or invented data"; "a zero drawn where the measurement is missing") | None has a data source or an honest definition. Replaced (deviation from baseline; "n of m judged normal") or dropped (UR.4). |
| C15 | **Flow Collectors 3/3, Event Processors 4/4** in the sidebar footer | Same | No endpoint, no model. Not shown (a gap, UR.3.4). |
| C16 | **"Reports"** nav item; **user profile**; **notifications dropdown** | `lib/nav.ts` ("only routes that exist"); D-10 (bell links to `/alerts`) | No `/reports` route; no identity endpoint; the bell is a link with a count. |
| C17 | **Sidebar tone**: the screenshot's selected item is a bright grey block | Suggestion §3 (restrained) | The restrained version (UR.3.2). No collision with a rule; recorded for the reviewer. |

Collisions C1 and C2 are the two the task named. C3 to C16 are the others found.

---

## UR.10 Questions the user must answer before Phase 1

Few, and only real ones. Each has the default this plan will use if the user says nothing.

1. **Where does the user land, and what is `/behavior` now?** The screenshot is one page that
   mixes behavior with offenses and fleet. This plan makes `/` the Dashboard and keeps `/behavior`
   as the separate behavioral page. Alternative: make the Dashboard live at `/behavior` and
   retire `/` (its tests would move). **Default: `/` is the Dashboard; `/behavior` stays.**
2. **Sidebar icons.** The screenshot has them; your standing rule (`AGENTS.md` §3a, 2026-10-12)
   bans decorative icons. A small, single-weight, inline-SVG set used only for navigation (no icon
   library, about 16 glyphs) would be functional wayfinding, not decoration. **Default: text-only
   navigation, no icons.**
3. **Dashboard ownership before OIDC.** Every request is `local-dev` today (or `local-token`,
   read-only), so "per user" dashboards are shared by everyone until Phase 4 OIDC. Persistence is
   built against `owner_subject` and works the day OIDC lands. **Default: build now, keyed on the
   principal's `subject`, accept the shared owner until OIDC.**
4. **A `GET /me` endpoint** (subject, email, permissions of the caller) is a small addition the
   catalog needs to hide widgets a role cannot read, and the shell needs for a user menu. Without
   it the catalog shows everything and a widget learns about a missing permission from a `403`.
   **Default: add it (B-1.3).**
5. **`/config-changes`** is a "Planned for Phase 3" placeholder with no backend. Restyle it, or
   remove the route? **Default: restyle only.**
6. **Light mode.** The plan keeps one dark theme (UR.8.5). **Default: no light mode.**
7. **Two binding hex values change for contrast** (UR.2.2): muted text `#73757B` → `#8C8E94`
   (the original fails AA at 3.70:1 on the panel), and a stronger input border `#6E7077`. The
   original `#73757B` survives for disabled text. **Default: apply both.** *Resolved by D-11
   (2026-10-14): neither deviation is applied — the palette's values shipped (UR.2.2).*

Not questions (decided by D-10 or by the code): the map is built behind the geo collection; EPS by
class replaces categories; the top bar uses existing APIs; `react-grid-layout` is allowed.

---

## UR.11 Summary of what each approval unlocks

- **Approve the plan** → UI-1.1 can start once T-070b is merged.
- **Approve a phase's screenshots** → the next phase starts (one phase at a time, as the
  suggestion and the orchestrator's working model require).
- **The geo track** needs only the T-077 report, then G-1.
