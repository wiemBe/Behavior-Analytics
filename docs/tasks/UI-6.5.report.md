# UI-6.5 report — `PRODUCT-UX-GUIDELINES.md` brought up to the shipped redesign

Task: [`docs/tasks/UI-6.5.md`](UI-6.5.md). Documentation only: no code, no test, no CSS was
changed. Every rule written was verified against the frontend source before it was written; the
proving file is named for each ruling below.

## 1. Files changed

| File | Change |
|---|---|
| `docs/PRODUCT-UX-GUIDELINES.md` | Brought up to the shipped redesign: the `/` Dashboard entry and the supporting-route entries in §3; the C1 KPI-row ruling in §2; the D-11/C3 statement in §1; new §8 component contracts (top bar, `WidgetFrame`, `useApiResource`, `DashboardView`/registry, `EditGrid`, `Catalog`/`Configure`/`ExpandDialog`, `RangeControl`, `useDashboard`) and extensions to `AppNav`, `PageHeader`, `StatCard`; §9 dashboard-grid rules; §10 C4 times, 24 px targets, mounted-only ARIA relationships, table names; **new §11 "Tokens and colour"** (status vs category colour, severity chart scale, the map ramp, solid fills, cyan accent, the literal ban with its contract test, the bounded D-11 AA exception, self-hosted Inter); §12 Testing (renumbered from §11) gained the registry/state/CSS-contract bullets; §13 Change checklist (renumbered from §12) gained seven checks. |
| `docs/tasks/UI-6.5.report.md` | New. This report. |
| `frontend/src/app/shellFixes.test.ts` | Review round 1: one comment line, "(guidelines §11)" → "(guidelines §12)". No code changed. |
| `frontend/src/app/nowrapContract.test.ts` | Review round 1: one comment line, "(guidelines §11)" → "(guidelines §12)". No code changed. |
| `frontend/src/app/controlTargetContract.test.ts` | Review round 1: one comment line, "(guidelines §11)" → "(guidelines §12)". No code changed. |
| `docs/tasks/UI-6.2.report.md` | Review round 1: its scope line cited the guidelines' old "§9–11" (Testing expectations, renumbered to §12); now reads "§9–10 and §12". Prose only. |

`git diff --stat`: `docs/PRODUCT-UX-GUIDELINES.md | 346 ++++++...` (340 insertions, 6 deletions);
nothing else tracked changed (`docs/tasks/UI-6.5.md`, the task spec, was already untracked and is
untouched).

## 2. C1–C17 and D-11: written where, proved by what

| # | Ruling | Written | Section(s) | Proof in the code |
|---|---|---|---|---|
| C1 | A four-card KPI row is allowed on `/` only, each card a different structure with a breakdown from real fields, a drill-down, honest states, no vanity delta | yes | §2 (new paragraph under "Investigation before decoration"), §3 `/` | `frontend/src/components/home/KpiRow.tsx` ("four cards of four different structures"), `KpiLogSources.tsx` (breakdown + qualifiers + drill-down link), `components/home/WidgetFrame.tsx` (states, link) |
| C2 | The donut is EPS by source class: top 4 classes + "Other" = five slices, with the list beside it as the text carrier | yes | §11 ("No chart relies on hue alone") | `frontend/src/lib/home/epsByClass.ts` (five-entry fold, `REST_LABEL`), `components/home/EpsByClass.tsx` (`aria-hidden` donut, the list beside it) |
| C3 | `/` is the cross-cutting Dashboard; `/behavior` stays the behavioral triage page; behavioral widgets get the larger area; offenses one KPI + one table | yes | §1 (new paragraph), §3 `/` | `frontend/src/app/page.tsx` + `widgets/templates.ts` (`SOC_OVERVIEW`: 8 of its 11 widgets are behavioral and the widest cell — 8 of 12 columns — is the fleet EPS chart; `kpi.offenses` + `table.recent_offenses` are offenses' only cells), `lib/nav.ts`, and the `/behavior` page tests unchanged |
| C4 | Absolute UTC time with the zone; the age may follow in muted text, never alone; `time` elements | yes | §10 (timestamps bullet extended) | `components/home/WidgetParts.tsx` (`When`: `time[datetime]` + `formatAgo` in `.tw-sub`), `lib/home/format.ts`, `components/shell/AutoRefresh.tsx` (`formatClockUtc`) |
| C5 | No tagline, no marketing subtitle, no version footer; the description is a fact (window, data age) | yes | §8 `PageHeader` (extended), §3 `/` | `components/ui/PageHeader.tsx` ("one line of orientation, not documentation"), `components/home/HomeDashboard.tsx` (`TEMPLATE_DESCRIPTION` is the window and "Times are UTC."), `app/layout.tsx` (brand carries no tagline; no version element exists) |
| C6 | No "Live"/"real-time" anywhere; auto-refresh shows the refreshed time and the data age | yes | §8 `AutoRefresh` | `components/shell/AutoRefresh.tsx` (Off/1 min/5 min; "Refreshed …" and "Data to …"; pauses hidden/dialog); a source-wide grep for "live"/"real-time" copy finds none |
| C7 | Status colour is not decoration: categorical charts use `--chart-cat-*`; the map uses the one-hue `--map-*` ramp; sparklines neutral; no tinted icon tiles | yes | §11 | `frontend/src/app/tokens.css` (`--chart-cat-1…5`, `--map-0…4`), `lib/contrast.test.ts` ("never uses a status hue as a categorical series"), `components/home/kpi.css` (sparkline stroke `var(--chart-expected)`, no fill). **Gap:** the map widget is not built — `widgets/registry.ts` lists `map.geography` as `blocked` — so the ramp's only consumer does not ship; the rule states this |
| C8 | No icons in the nav, KPI cards or tables; source type is its name; navigation is text only | yes | §8 `AppNav` (extended, cites D-11) | `components/shell/AppNav.tsx` (text `Link`s only), `lib/nav.ts`; no icon component or icon font exists in `frontend/src`; `TopSources.tsx` renders the type as text (`typeLabel`) |
| C9 | No global time range: the top-bar range of the plan was not built; the dashboard's window belongs to the dashboard and page-local selectors keep their contracts | yes (as shipped, see note) | §8 `RangeControl`, §3 `/` | `components/shell/TopBar.tsx` (search, refresh, bell, status — no range element), `widgets/RangeControl.tsx`, `widgets/ranges.test.tsx`, `widgets/registryRange.test.ts`. Note: UR.9's ruling allowed a top-bar range "only on routes that register support"; the shipped code resolves the collision more strictly — there is none, and the window is per-dashboard (`layout.range`) with per-widget overrides |
| C10 | Solid alpha fills only, no gradients or glows; one shadow token | yes | §11 | `tokens.css` (`--chart-band` rgba, `--shadow` single), `app/colourLiterals.test.ts`; the only gradients in CSS are the table-scroll covers/shadow (`globals.css`, `components/home/tables.css`) — functional scrolling affordances, stated as the exception |
| C11 | Superseded by D-11: keep `--text-3 = #73757B` and `--border-strong = #35363A` | yes, as D-11 | §11 "The D-11 contrast exception, and its exact bounds" | `tokens.css` header comment (D-11), `lib/contrast.test.ts` (pins `#73757B` and `#35363A`, asserts no `--text-disabled`) |
| C12 | `--info` becomes cyan, the one data accent; the primary button and the focus ring are neutral | yes | §11 ("Cyan is the one data accent") | `tokens.css` (`--info: #6fd0e0`, `--primary`/`--primary-hover` light, `--focus: #e8e8e8`, `--link: var(--info-text)`, `--control-accent: var(--neutral)`) |
| C13 | Pills keep one `crit` tone for CRITICAL and HIGH with the word; charts use `--sev-*` (HIGH a darker red); no orange | yes | §11 ("Severity colour in charts"), on top of the existing §10 rule | `lib/api.ts` `sevTone` (CRITICAL and HIGH → `crit`), `lib/statusSemantics.test.ts`, `tokens.css` (`--sev-critical #e36565`, `--sev-high #a64b4b`), `lib/home/chartPalette.ts` (`SEVERITY_TOKEN`) |
| C14 | No delta without a baseline; no "97.4 % healthy"; deviation-from-expected replaces "Δ (24h)" | yes | §3 `/` (the "must not become" paragraph) | `KpiLogSources.tsx` ("n of m judged normal"; `judgedText` excludes unjudged), `TopSources.tsx` (deviation from expected, em dash when `expected_eps` is null), `lib/home/logSources.ts`; no percentage-of-health or previous-window delta exists |
| C15 | "Flow Collectors 3/3", "Event Processors 4/4" are not shown | yes | §8 `ConnectionStatus` | `components/shell/ConnectionStatus.tsx` (three named signals; the comment names the two omitted suggestions) |
| C16 | No "Reports" nav item, no user profile, no notifications dropdown; the bell is a link with a count | yes | §8 `AlertBell` (+ §3 supporting-routes preamble for entry-exists) | `lib/nav.ts` (no `/reports`; only existing routes), `components/shell/AlertBell.tsx` (count → `/alerts`; failed read "unavailable", never `0`), `TopBar.tsx` (four elements, no user menu) |
| C17 | Restrained sidebar selection: raised surface + neutral rule, never a bright block or blue | yes | §8 `AppNav` (last sentence) | `app/globals.css` `.nav-link[aria-current="page"]` (`--surface-2`, 2 px `--neutral` left rule, comment "never blue") |
| D-11 | `/` is the widget Dashboard and `/behavior` stays; navigation text only; dark only, no light mode; the two palette values kept with the explicit, bounded AA exception | yes | §1, §3 `/`, §8 `AppNav`, §11 (dark-only + the exception subsection) | `app/page.tsx` (Dashboard), `lib/nav.ts` (text entries), `tokens.css` (`color-scheme: dark`; no `prefers-color-scheme` rule anywhere — verified by grep; the two values), `app/shellFixes.test.ts` (pins `color-scheme: dark`), `lib/contrast.test.ts` |

The exception is stated as covering **exactly** `--text-3 = #73757B` and `--border-strong =
#35363A` and nothing else, per the task's requirement.

## 3. Existing rules changed or removed

**No existing rule was removed, and none had its meaning reversed.** Every edit to existing text
is an extension. The complete list:

1. §2 "Investigation before decoration" — added the C1 paragraph after it. The existing sentences
   are untouched. Evidence the old text still holds: `/behavior` still carries exactly four
   counters (`app/behavior/page.test.tsx`), unchanged by the redesign.
2. §8 `AppNav` — added the text-only/entry-exists/selection paragraph (D-11, C8, C16, C17). The
   `aria-current` contract paragraph is untouched (`components/shell/AppNav.test.tsx` still pins
   it).
3. §8 `PageHeader` — added the C5/C6 description rule and the empty-`meta` fact. The `h1`
   ownership sentences are untouched (`components/ui/PageHeader.tsx`).
4. §8 `StatCard` — added the note that the Dashboard KPI row is `WidgetFrame`'s `kpi` variant,
   not `StatCard`. The existing limits stand (`/behavior` and the detail pages still use
   `StatCard`; `frontend/src/components/StatCard.tsx`).
5. §10 timestamps bullet — extended with C4 (relative age secondary, `time` elements). The UTC
   and zone requirement is unchanged (`lib/health.ts` `formatDateTime`/`formatDateTimeCompact`).
6. §10 tables bullet — appended the source-wide table-name fact (`components/ui/TableNames.test.ts`,
   added by UI-6.2). The semantics requirement is unchanged.
7. §11 Testing → renumbered **§12**, gained two bullets (registry/widget states, CSS contracts).
8. §12 Change checklist → renumbered **§13**, gained seven checks. All twelve existing checks are
   verbatim.
9. Section numbering: the new "Tokens and colour" was inserted as §11 after §10 Accessibility, so
   Testing (11→12) and Change checklist (12→13) moved. This placement was chosen because every
   live cross-reference to §1–§10 — including `AGENTS.md` §3a's "(guidelines §8)", "§9", "§10"
   and the code comments citing §3, §4, §8, §10 — stays correct. See Open points for the three
   code comments that cite the old §11.

Rules verified as still true and left alone: §4 (bucket semantics — `VolumeChart`,
`lib/timeseries.ts`), §5 (lifecycle — `lib/api.ts`, `statusSemantics.test.ts`), §6 (evidence and
the three-contributor dimension-diverse headline — `lib/contributors.ts`), §7 (prohibited
language — `lib/summarize.ts`), the `Tabs`/`Disclosure`/`CodePanel`/`TableScroll`/
`EvidenceBanner`/`DimensionExplorer`/`LifecycleTimeline`/`TechnicalDetails`/`IncidentMetrics`/
`LoadingState`/`ErrorState` contracts (each still pinned by its test file in `components/ui/`,
`components/behavior/`).

## 4. Proposed text for `AGENTS.md` §3a

A short diff. Everything not named stays as it is.

**Changed bullets** (old → new):

1. - old: `- a row of identical KPI cards holding vanity numbers;`
   - new: `- a row of identical KPI cards holding vanity numbers — a KPI row is allowed on the
     Dashboard only, when each card carries a breakdown from real fields and drills down
     (guidelines §2);`
2. - old: `- a chart with no unit on its axis, a library-default chart style (a marker on every
     point, rainbow series), or a pie chart with more than five slices;`
   - new: `- a chart with no unit on its axis, a library-default chart style (a marker on every
     point, rainbow series), a pie chart with more than five slices, or a status hue used as a
     categorical series colour (guidelines §11);`
3. - old: `- Times show their timezone.`
   - new: `- Times show their timezone; a relative age may follow the absolute UTC time, never
     replace it (guidelines §10). No copy says "live" or "real-time".`
4. - old: `- No new colour, spacing or type literal outside `tokens.css`.` (under "Build from
     what exists")
   - new: `- No new colour, spacing or type literal outside `tokens.css` — the colour half is
     enforced by `frontend/src/app/colourLiterals.test.ts` (guidelines §11).`

**Added bullets:**

Under **Build from what exists:**

- `- Reuse the redesign's shell and widget system (guidelines §8): the top bar, `PageHeader`,
  `WidgetFrame` and the widget registry before any new frame or card.`
- `- The product is dark only; navigation is text only (decision D-11). The palette keeps
  `--text-3 = #73757B` and `--border-strong = #35363A` although the first is below WCAG AA: the
  user's explicit exception for exactly these two values and nothing else (guidelines §11).`

Under **Always:** (after the honesty-states bullet)

- `- A Dashboard widget renders every state itself — loading, empty, no data, not collected,
  stale, error, forbidden naming the permission — and drills down to the route that owns its
  data (guidelines §8).`

## 5. What I did (against the acceptance criteria)

1. **UR.9 rulings** — each of C1–C17 was located in `docs/UI-REDESIGN-PLAN.md` UR.9, verified
   against the code, and written into the section where an agent looks for it (§2/§3/§8 for
   structure, §10 for times, §11 for colour, per the task's own examples). No ruling was written
   that the code does not implement; the one partial case (C7's map ramp) states in the rule that
   the widget is not built, and C9's divergence from the plan (no top-bar range exists at all) is
   recorded in the table above.
2. **D-11** — dark only, text-only navigation, and the two palette values with the explicit,
   bounded AA exception are in §1, §3, §8 and §11; the exception's bounds are their own
   subsection.
3. **§3** — the `/` entry (question, what it carries, what it must not become, with C3's
   larger-area rule), plus one short entry per other shipped route group, each naming its
   question. The route list was taken from `find frontend/src/app -name page.tsx` (33 pages);
   nothing is described that does not exist — `/behavior/waf`, fleet, offenses, alerts, rules,
   coverage, log-sources, searches (including `/searches/new`), the admin hub and its five
   surfaces, and `/config-changes` (not collected, not in nav). IPS anomalies are reached through
   `/anomalies` and the `ips.source_anomalies` widget; no `/behavior/ips` route exists, so none is
   listed.
4. **Tokens and colour section** — new §11 with everything the task names, including the
   contract-test citation (`colourLiterals.test.ts`, plus the same sweep inside
   `contrast.test.ts`). Spacing and type literals are stated as review-held, not test-enforced,
   because only the colour half has a test — verified before writing.
5. **§8** — the shared components the redesign introduced, under their real names
   (`TopBar`…`ConnectionStatus`, `WidgetFrame`, `useApiResource`, `DashboardView` + registry,
   `EditGrid`, `Catalog`, `Configure`, `ExpandDialog`, `RangeControl`, `useDashboard` family),
   each with the invariant its code and tests hold.
6. **§9/§10** — only what the QA phase made true: the 24 px target minimum (`--target-min`,
   `controlTargetContract.test.ts`, UI-6.6 point 4), the mounted-only `aria-controls` rule
   (UI-6.2), the source-wide table names (UI-6.2), C4 times, the dashboard grid's derived
   columns, its single accepted inline style, and the off-screen body deferral (UI-6.7).
7. **§12 checklist** — seven new one-line checks (§13 after renumbering).

## 6. Gates

- `git status --short` (after review round 1) → modified: `docs/PRODUCT-UX-GUIDELINES.md`,
  `docs/tasks/UI-6.2.report.md`, `frontend/src/app/controlTargetContract.test.ts`,
  `frontend/src/app/nowrapContract.test.ts`, `frontend/src/app/shellFixes.test.ts`; untracked:
  the pre-existing task spec `docs/tasks/UI-6.5.md` (untouched) and this report.
- `git diff --stat` (after review round 1) →

  ```
  docs/PRODUCT-UX-GUIDELINES.md                  | 346 ++++++++++++++++++++++++-
  docs/tasks/UI-6.2.report.md                    |   2 +-
  frontend/src/app/controlTargetContract.test.ts |   2 +-
  frontend/src/app/nowrapContract.test.ts        |   2 +-
  frontend/src/app/shellFixes.test.ts            |   2 +-
  5 files changed, 344 insertions(+), 10 deletions(-)
  ```

  The guidelines diff is 340 insertions and 6 deletions; the 6 deleted lines are the two
  renumbered section headings (`## 11. Testing expectations`, `## 12. Change checklist`) and four
  lines whose sentences were extended and rewrapped (the companion-documents line, `PageHeader`'s
  `h1` sentence, the §10 timestamps and tables bullets). No rule text was removed. The other four
  files carry the round-1 reference fixes (one line each).
- No code gates apply (no executable code changed — the three test-file edits are comment lines):
  `ruff`, `pytest`, `npm run lint/typecheck/test` were not run. An attempt to run the three
  edited test files could not start: this worktree has no `node_modules` (each UI phase ran
  `npm ci` in its own tree) and this environment's npm cache is read-only. The diffs above show
  the edits are one `//`-comment character change each, with no code, import or assertion
  touched.

## 7. Open points

1. **Stale "(guidelines §11)" comments — fixed in review round 1.** The three code comments
   (`frontend/src/app/shellFixes.test.ts:2`, `frontend/src/app/nowrapContract.test.ts:2`,
   `frontend/src/app/controlTargetContract.test.ts:3`) now read "(guidelines §12)". A
   repository-wide sweep (excluding `docs/PLANNER.md`, `node_modules`, `.next`) found exactly one
   further stale reference — `docs/tasks/UI-6.2.report.md` citing the guidelines' old "§9–11" —
   fixed to "§9–10 and §12". Every other §11/§12 hit in the sweep is another document's own
   section (the WAF design, Phase A, the handoff documents), and every other guidelines citation
   names §3–§10, whose numbers did not move.
2. **The geography widget is not built.** `map.geography` is `blocked` in
   `widgets/registry.ts` (its `blockedBy` string is pinned by `registry.test.ts`), while the
   backend aggregate and `GET /geo/countries` exist (tasks G-2, G-2b) and `--map-0…4` are in
   `tokens.css`. §11 states this inside the rule; building the widget is the remaining work.
3. **C9 as shipped is stricter than UR.9's ruling.** The plan's top-bar range control was never
   built; the dashboard's window is per-dashboard with per-widget overrides. The guidelines
   describe the code.
4. **Pre-existing quirk left alone:** §3 `/behavior/sources/[id]` says "Answers six questions"
   but lists seven numbered items (the header plus six). The wording predates this task; the
   section order itself is what the page-composition tests pin, so changing the count was not
   part of this task.
5. The registry does not (as UR.5.4 proposed) assert that every widget handles every state;
   coverage is the frame's state union at both densities (`WidgetFrame.test.tsx`) plus each
   catalog widget's loading/error/forbidden (`components/catalog/states.test.tsx`). §12 was
   written to the shipped coverage.
