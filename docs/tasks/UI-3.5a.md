# Task UI-3.5a — the catalog widgets that need no configuration

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a (the frontend quality
bar) above all**. Then read `docs/PRODUCT-UX-GUIDELINES.md` (normative),
`frontend/src/app/tokens.css`, and the widget system UI-3.1 built (`frontend/src/widgets/*`,
`docs/tasks/UI-3.1.report.md`): the registry, `WidgetDef`, the `WidgetState` union, the catalog,
`DashboardView`. Build on them; do not change their contracts.

## Binding design

`docs/UI-REDESIGN-PLAN.md` UR.4.1 (common rules for every widget), UR.4.4 and UR.5.4 (states),
UR.5.1 (the catalog table: data, permission), UR.5.6 (permissions). Each widget answers **one
question** and its first lines answer it. Small: a widget is a glance with a drill-down link to
the page that has the detail, not a copy of that page.

## The widgets (register each `live`, with its real `sources` and `requires`)

| Type | Question it answers | Data | Permission | Drill-down |
|---|---|---|---|---|
| `health.detection` | Is detection coverage holding up? | `/coverage/summary`, `/rules/health-summary` | `read:coverage`, `read:rules` | `/coverage`, `/rules` |
| `health.collection` | Are the scheduled searches running on time? (per family: cadence, last run, how late) | `/admin/search-cadence` | `read:search_cadence` | `/admin/search-cadence` |
| `health.evidence` | Is evidence collection keeping up? (pending and failed counts) | `/anomalies/summary` (`evidence_pending`, `evidence_failed`) | `read:anomalies` | `/anomalies?evidence_status=FAILED` |
| `table.catchall_senders` | Which unknown senders feeding the catch-all need review? (the newest, bounded) | `/fleet/catchall/senders?review_status=NEW` | `read:fleet` | `/fleet/catchall` |
| `table.fleet_classes` | How does each source class fare? (sources, EPS, silent) | `/fleet/summary` | `read:fleet` | `/fleet` |
| `table.outage_policy` | Which outage policy applies, and how many overrides? | `/fleet/summary` (`outage_mode`, `outage_overrides`) | `admin:outage` | `/admin/outage-policy` |
| `waf.sources` | Are the WAF sources collecting? (profile status, coverage, active endpoints against the ceiling) | `/behavior/waf/sources` | `read:anomalies` | `/behavior/waf` |
| `table.recent_waf_anomalies` | Which WAF endpoints are anomalous now? | `/anomalies?subject_kind=WAF_ENDPOINT&active_only=true` | `read:anomalies` | `/anomalies?subject_kind=WAF_ENDPOINT&active_only=true` |

Read each backend schema (`backend/app/schemas/*`) before designing a widget, so every number
shown is one the API really returns, with its unit, and every unmeasured value is an em dash, not
a zero. For `table.recent_waf_anomalies`, reuse the Home's `RecentAnomalies` with a subject
parameter rather than writing a second table (wrap, do not fork). Choose each widget's category
(`Health`, `Table`, `Admin`) and default size so that its content fits its box at 12 columns
without scrolling; state the sizes in the report.

Every widget renders through the one `WidgetFrame` and reads through `useApiResource`
(add `homeReads` entries in `api.ts` with an `AbortSignal`, as the Home's do). Every state:
loading; empty (what is empty and why); error (what failed, what to do); forbidden (the permission
named); and the honesty states that apply (`INSUFFICIENT_DATA`, `PARTIAL`, `TRUNCATED`,
`NOT_COLLECTED`).

**None of these widgets joins the SOC Overview template**: the Home does not change. They appear
in the catalog, and Customize can add them.

**One carried fix.** In the catalog, "Already on this dashboard" uses the warning colour for
neutral information. Show it in the muted text style.

## Tests (Vitest)

Per widget: the ok rendering with its numbers and units; each state above; the drill-down link.
The registry test (UI-3.1) must stay green with the new entries (it checks sources against
`api.ts` and permissions against `rbac.py`). Break at least one guard per widget, confirm a test
fails, restore, and list them.

## Screenshots (required)

Fixture API outside the repository: copy and extend
`/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/a16a3a57-0bb1-488e-a4d5-dadbc9e16dfd/scratchpad/ui31/`
(`fixture.mjs`, `capture.py`) into `.../scratchpad/ui35a/`, with realistic fixture data for every new
route; ports **3135** (Next), **8135** (fixture), Chrome debugging **9135**. Through Customize → Add
widget, place all eight on the Home, then capture at 1440×900 and 1280×800 (full page), plus each
widget's empty and error state at 1440 (a crop or a full page per state is fine), and the catalog
at 1440 showing the new entries. Look at every PNG yourself and list them with what each shows.
Fix what looks wrong before reporting (a widget half empty, clipped or scrolling content, colliding
labels, a zero where nothing was measured).

## Allowed files

- `frontend/src/widgets/*` (registry entries; the catalog colour fix)
- `frontend/src/components/catalog/*` (new: the widget components and their tests)
- `frontend/src/components/home/RecentAnomalies.tsx` (a subject parameter only) and its test
  (additions only)
- `frontend/src/lib/api.ts` (`homeReads` entries only)
- `frontend/src/app/tokens.css` (new tokens only)
- `docs/tasks/UI-3.5a.report.md` (new)

No new dependency. `frontend/node_modules` is a symlink to the main checkout's: never
`npm install`. Never `pkill -f` or `killall`; stop only the processes you started, by PID.

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim into
`docs/tasks/UI-3.5a.report.md` (AGENTS.md §6 format), with the PNG list. Do not commit.
