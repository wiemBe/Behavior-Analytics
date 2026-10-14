# Task UI-R0 — UI redesign, Phase 0: repository assessment and `docs/UI-REDESIGN-PLAN.md`

Read `AGENTS.md` at the repository root first and follow it exactly.

**This task writes one document. Change no application code, test or configuration.** The user
approves the plan before any implementation starts.

## What the user asked for

- Read [`docs/UI_Suggestions/UI_Suggestion.md`](../UI_Suggestions/UI_Suggestion.md) in full, and
  look at the home reference screenshot `docs/UI_Suggestions/QRadar Observability Security
  Dashboard.png`.
- Then read, in `docs/PLANNER.md`:
  - the seventh-session note in "▶ Resume here", which records the user's own words: mostly UI
    polish, the palette and design applied everywhere, and no overhaul of existing work without
    asking;
  - decision **D-10** in §4, which holds the user's answers on the four items that go beyond
    polish.
- File paths named in the suggestion may not exist. Map them to what does exist, and do not get
  stuck on them.

What is binding and what can be adapted:
- **Binding:** the charcoal palette and the semantic colours. There must be no blue- or
  purple-tinted background.
- **Binding:** the home layout and composition follow the screenshot.
- **Binding:** D-10.
- **Binding:** `AGENTS.md` §3a and `docs/PRODUCT-UX-GUIDELINES.md` (normative), except where the
  user's request explicitly changes them. Where they collide, name the collision and propose a
  ruling. The orchestrator decides.
  - Two collisions are known:
    - the screenshot's KPI row: four cards;
    - the screenshot's six-slice donut, which D-10 already replaces.
  - Find any others.
- **Adaptable:** the suggestion's generic parts, where the existing product already answers them
  better. Say so, with the reason.

## Assess (Phase 0 of the suggestion)

Inspect the repository:
- `frontend/`: Next.js 15, React 19, ECharts (`echarts-for-react`);
- `frontend/src/app/tokens.css` and `frontend/src/app/globals.css`;
- `frontend/src/components/**`;
- `frontend/src/lib/api.ts` and `frontend/src/lib/nav.ts`;
- the backend routes and schemas behind each page;
- the auth and RBAC model (`backend/app/security/`);
- the tests (Vitest), and the screenshot workflow in `docs/UI-WORKSTREAM-COMPLETION.md` §4.

Every route that exists today:

```
/  /admin  /admin/classification-rules  /admin/ips-sources  /admin/ips-sources/[logSourceId]
/admin/search-cadence  /admin/waf-sources  /admin/waf-sources/[logSourceId]  /alerts  /alerts/[id]
/anomalies  /anomalies/[id]  /behavior  /behavior/sources  /behavior/sources/[id]  /behavior/waf
/behavior/waf/sources/[id]  /config-changes  /coverage  /fleet  /fleet/sources  /log-sources
/log-sources/[id]  /offenses  /offenses/[id]  /rules  /rules/[id]  /rules/statistics  /searches
/searches/[id]
```

Two routes are being built in parallel and belong in the inventory: `/admin/outage-policy`
(T-070a) and `/fleet/catchall` (T-070b).

## Write `docs/UI-REDESIGN-PLAN.md`

Match the voice of the existing design docs: plain, specific, with numbered sections. It must
contain the following.

1. **Page inventory.** For every route:
   - the one question it answers;
   - its data sources (exact API routes);
   - its honesty states;
   - what the redesign changes;
   - what it must keep: the invariants and the pinned tests that guard them.
2. **Design tokens.**
   - Map every existing token in `tokens.css` to its new value, including the semantic colours and
     the severity scale.
   - Choose a chart palette that is not blue-dominant and is distinguishable for colour-blind
     readers.
   - Check WCAG AA contrast for each text and background pair; give the ratios.
   - Typography: Inter **self-hosted**. The product is on-premises, so there are no Google Fonts
     at runtime. Say how, and what the fallback is.
   - The tokens remain the only home for literals.
3. **Shell and navigation.**
   - The sidebar groups and the selection style.
   - The top bar per D-10: search over existing lists; auto-refresh with the last-updated time;
     the open-alert count linking to `/alerts`; the time range where a page supports it; the
     connection status from existing health APIs. Name the APIs.
   - All existing routes are preserved.
4. **Home.**
   - Each widget in the screenshot mapped to real data. Name the API and fields; mark any gap.
   - The "logic" behind each widget, meaning what makes it useful to an analyst rather than
     decorative. Examples: KPI cards that drill down and carry breakdowns; observed against
     expected EPS with the band and anomaly markers; top sources with their deviation from
     baseline; EPS by source class (top 4 plus "Other", D-10); the geo map; recent anomalies;
     recent offenses.
   - The states of each widget.
5. **Widget system** (D-10: the full system now).
   - A registry and catalog.
   - `react-grid-layout`: the exact version, its React 19 compatibility, and SSR concerns.
   - Drag, resize, configure, expand, restore defaults, and per-widget time range.
   - Persistence: a proposed table or tables, a versioned layout JSON schema, owner and sharing
     model, RBAC permissions, the API routes, audit, the migration (additive, AGENTS.md rule 7),
     and limits (maximum widgets, maximum dashboards).
   - Templates: SOC Overview, QRadar Administration, Behavioral Analytics, Log Source Monitoring,
     WAF/IPS Monitoring. Each must contain only widgets with real data.
   - What a widget does when its permission is missing.
6. **Geo collection** (D-10). Propose:
   - a live, read-only validation task for the AQL geo functions on the lab QRadar (which
     functions, which fields, cost, and how null or private ranges behave);
   - then a collector: country-level aggregate only, under the search cadence, fail-closed,
     constant SQL. No raw IP is ever stored (AGENTS.md rules 3, 8 and 9).
   - Name the design questions that the live run must answer first.
7. **Phases and task split.**
   - Phase 1 foundation (tokens, typography, shell); Phase 2 home; Phase 3 widget system;
     Phase 4 persistence; Phase 5 every remaining page; Phase 6 QA. The geo work runs in
     parallel.
   - Each phase is split into tasks small enough for one agent, with the allowed files,
     acceptance criteria and the screenshot captures required.
   - Mark which tasks are UI (Sonnet or Codex only) and which are backend.
8. **Risks.** Cover at least:
   - test churn from the restyle (which pinned tests are affected, and how to keep their intent);
   - §3a collisions and their proposed rulings;
   - performance with many widgets;
   - SSR and hydration;
   - the 1024×768 viewport;
   - dark/light: is light mode kept? Propose an answer;
   - the work in flight (T-070a, T-070b, IPS UI) landing during the restyle.

## Allowed files

- `docs/UI-REDESIGN-PLAN.md` (new). Nothing else.

## Environment

Read-only on the main checkout, `/home/efe/Documents/QRadar-Dash`, except for the one new file.
Run no git command that changes state.

## Report

End with a short report:
- the plan's section list;
- the collisions found, with your proposed rulings;
- the questions the user must answer before Phase 1. Keep them few, and only real ones.

Do not commit.
