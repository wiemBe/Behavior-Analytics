# Task UI-3.5b — the configurable catalog widgets, and IPS anomalies on the dashboard

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a above all**. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative), `frontend/src/app/tokens.css`, `docs/UI-REDESIGN-PLAN.md`
UR.5.1 (the catalog table and its rules), and the widget system as built: `frontend/src/widgets/*`,
`frontend/src/components/home/WidgetFrame.tsx`, and the reports of UI-3.1, UI-3.2, UI-3.3,
UI-3.3b and UI-3.5a in `docs/tasks/`.

## Why

UR.5.1 lists three widgets that are not built yet:
- `waf.endpoint_traffic`: one WAF endpoint's requests per bucket against its seasonal band. Its
  config is the endpoint.
- `chart.eps_observed_expected` in **source mode**: one log source's EPS against its expected
  band. Its config is the source. Fleet mode is the existing `chart.eps_fleet_total`.
- `ips.source_anomalies`: blocked "until the IPS analyst API (T-085) and its UI". Both are
  merged now (T-085, T-087).

UI-3.3 built the configure drawer and its generic form. Its `ConfigSchema` options are **static**
lists, though, and an endpoint or a source is an id from a read.

## Build

1. **Options from a read.** Extend `ConfigSchema` so a key's options come either from a static list
   (as today) or from **one existing GET read**. The read returns the candidate ids with their
   labels: `/behavior/sources` (monitored log sources), `/behavior/waf/sources` (WAF sources), or
   `/behavior/waf/sources/{log_source_id}/endpoints` (one WAF source's endpoints, paged).
   - **There is no fleet-wide endpoint list.** So a key's read may depend on another key: a
     cascade. The endpoint picker reads the endpoints of the WAF source chosen above it, and
     changing the source clears the endpoint.
   - Before typing each route, check its real path, parameters, paging and response shape in
     `backend/app/api/routes/anomalies.py` and `backend/app/schemas/*`.
   - The drawer loads it through `useApiResource`, the same way widgets read.
   - It renders a labelled select over the returned ids. When the list is long, a **client-side**
     filter input above it narrows the options. That text never leaves the browser: it is not a
     query parameter, not a request body, and not stored in the layout. A test pins this.
   - The read's own states are designed: loading, failed (say what failed; Apply disabled), and
     empty (say what is empty and why; Apply disabled).
   - The stored config holds the **id only**. A widget whose configured id no longer exists says
     so and offers Configure; it never draws another subject's data.
2. **`waf.endpoint_traffic`** (Chart):
   - config: `waf_source`, then `endpoint` (the cascade above); range: `1h | 24h | 7d`, mapped to
     the route's `since`/`until` within
     its validated ceiling;
   - reads `/behavior/waf/endpoints/{id}/metrics` and `/baselines`;
   - reuse the WAF request chart and its band rendering (`components/waf/*`) rather than copying
     them;
   - the title names the endpoint (method and path) and the range; drill-down to the endpoint's
     WAF view;
   - **not configured yet** is a designed state: "Choose an endpoint" with the Configure action
     in customize mode, and a plain sentence in read mode. It is not an error and not a chart.
   - `expandable: true`.
3. **`chart.eps_observed_expected`** (Chart, source mode):
   - config: `source` (a monitored log source); range presets as above;
   - reads that source's EPS metrics and baseline cells, and reuses the existing per-source
     behavior chart pieces (band, gaps, PARTIAL marks);
   - the title names the source; drill-down to `/behavior/sources/{id}`;
   - the same not-configured state; `expandable: true`.
4. **`ips.source_anomalies` becomes live** as "Recent IPS anomalies" (Table):
   - the latest active IPS anomalies of **both** kinds: two bounded reads,
     `/anomalies?subject_kind=IPS_ENTITY` and `...=IPS_SOURCE`, each with the limit the other
     recent-anomaly widgets use and `active_only`;
   - merged by detection time and cut to that limit. The top n of the union is always inside the
     union of the two top-n lists; say so in a comment;
   - each row uses T-087's IPS row rendering (`components/ips/*`), in the detector's unit and
     never EPS;
   - "n of N" from the two totals; drill-down `/anomalies?subject_kind=IPS_ENTITY` (or a
     combined link if the list page supports it; check, do not invent);
   - keep the type id `ips.source_anomalies` (no layout holds it while it is blocked), but update
     its title and description to what it shows.
5. **Duplicates.** Remove `Catalog.tsx`'s "Already on this dashboard" guard **for widgets that have
   config keys**: two endpoint-traffic widgets on two endpoints are meaningful. Keep it for
   widgets without config. Update the guard's comment.
6. Registry: the three types are `live`, with their real `sources` and `requires`. **Allowed
   existing-test edit:** the exact live-type list in `registry.test.ts` gains these three types.
   Name it in the report.

## Tests

- The option source: the drawer requests the read once; the filter narrows options and is never
  sent (assert every request URL); loading, failed and empty states, each with Apply disabled; the
  stored config is the id.
- Each new widget, through its states:
  - not configured;
  - loading;
  - failed;
  - empty range (absence of collection, not zero);
  - INSUFFICIENT_DATA (no reliable cell);
  - PARTIAL;
  - configured id gone;
  - the request URL carries the effective range.
- IPS table: the merge order; the cut to the limit; IPS rows never say EPS; "n of N".
- Duplicates: a configurable widget can be added twice, an unconfigurable one cannot.
- **Break-its (at least six):**
  - the filter text reaches a URL;
  - a gone id draws another subject;
  - not configured renders a chart;
  - the IPS merge drops the newest row;
  - an IPS row says EPS;
  - the duplicate guard removed for unconfigured widgets.

## Screenshots

Copy the newest fixture, `/home/efe/Documents/QRadar-Dash-work/ui33b/fixture/` (or `ui33/` if
`ui33b/` does not exist), to `/home/efe/Documents/QRadar-Dash-work/ui35b/fixture/`. Never edit the
original. Add WAF endpoints with metrics and baselines, monitored sources with metrics and
baselines, and IPS anomalies of both kinds; T-087's fixture at
`/home/efe/Documents/QRadar-Dash-work/t087/fixture/fixture_api.py` has IPS payloads to borrow.
- Ports: 3136/8136/9136.
- At 1440×900, 1280×800 and 1024×768: the Home with the three new widgets placed and
  configured.
- At 1440: the configure drawer with the endpoint picker filtered; each widget not configured;
  each with a gone id; each expanded.
- PNGs go to `/home/efe/Documents/QRadar-Dash-work/ui35b/png/`. Nothing goes under `/tmp`.
- Re-run the density measurement (`verify.mjs`, copied from the newest one) with the new widgets
  placed. Quote its output.
- Look at every PNG yourself.

## Allowed files

- `frontend/src/widgets/*` (+ tests)
- new widget components in `frontend/src/components/catalog/` (+ tests)
- `frontend/src/components/waf/*` and `frontend/src/components/ips/*`, only to export or
  generalize a piece for reuse, with existing behaviour and tests unchanged
- `frontend/src/components/behavior/*`: the same rule
- `frontend/src/lib/api.ts` (read helpers only, additive)
- `frontend/src/app/tokens.css` (new tokens only)
- `docs/tasks/UI-3.5b.report.md` (new)

No new dependency. No backend change: if a read is missing, stop and report it.

## Gates

From `frontend/` with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim into the report
(AGENTS.md §6 format), with the PNG list and the measurement output. Do not commit. Stop every
process you start, by PID. Never `pkill -f` or `killall`.
