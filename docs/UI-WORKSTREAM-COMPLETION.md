# UI Workstream — Completion Report

Final verification record for the behavioral analytics UI workstreams. The UX
contracts these workstreams established are documented in
[Product UX Guidelines](PRODUCT-UX-GUIDELINES.md); this document records that
they were verified, how, and with what result.

---

## 1. Scope completed

| Area | Outcome |
|---|---|
| Navigation and shell | Grouped navigation leading with the behavioral destinations; supporting capabilities demoted to their own labelled group; `aria-current="page"` on exactly one destination |
| Behavior overview | Reduced from ten equally weighted counters to four decision counters, a worklist, recently resolved, and a health distribution |
| Source inventory | New `/behavior/sources` route: searchable, sortable, filterable, server-paginated — moved off the overview |
| Anomaly triage | `/anomalies` reduced from eleven columns to nine; version stamps, duration and confidence moved behind a disclosure |
| Anomaly investigation | `/anomalies/[id]` rebuilt around a deterministic summary, four metrics, timeline, what changed, and tabbed investigation detail |
| Source behavior detail | `/behavior/sources/[id]` rebuilt into the seven-section hierarchy with four primary metrics and a dominant timeline |
| Responsive containment | `html { overflow-x: hidden }` removed and replaced with structural fixes; `.table-scroll` made a containing block |
| Test coverage | Hierarchy, evidence, lifecycle and accessibility regression coverage |
| Product UX guidelines | Canonical normative document for future contributors |

## 2. Commit chain

All commits below are in the current local ancestry.

| Workstream | Commit | Subject | Published |
|---|---|---|---|
| 1 — visual system | `01bcd86` | `refactor(ui): establish behavioral analytics visual system` | yes |
| 2 — overview + inventory | `12b0db8` | `feat(ui): simplify overview and add source inventory` | yes |
| 3 — triage + investigation | `0d6ee14` | `feat(ui): redesign anomaly triage and investigation` | yes |
| 4 — source behavior + layout | `1a76dac` | `feat(ui): refine source behavior and responsive layout` | yes |
| 4 — completion fix | `443d509` | `feat(ui): refine source behavior and responsive layout` | yes |
| 5 — test coverage | `6956a29` | `test(ui): cover hierarchy evidence and accessibility` | yes |
| UX guidelines | `9e6bd25` | `docs(ui): add product UX guidelines` | yes |
| Final verification | `34e056d` | `docs(ui): record final verification and completion` | **no** |

No defect-fix commit was required by this workstream: final verification found
no product defect.

## 3. Product invariants preserved

Each was confirmed in a real browser against the fixture data described in §4,
in addition to the automated suite.

- **Null is not measured zero.** An unmeasured value renders as an em dash. On
  the inventory, an unbaselined source shows `—` for observed and deviation
  while a silent-but-measured source shows `0.00`.
- **COMPLETE zero is a real observation.** The source page reports Observed EPS
  `0.00`, and the chart draws the complete-zero span as a line at zero.
- **MISSING and PARTIAL are not exact zero.** Both are plotted as null with the
  line broken across them, and the chart caption states that gaps are intervals
  that were not fully collected and are not zero traffic.
- **`INSUFFICIENT_DATA` is not healthy.** Its badge is neutral, never green,
  beside a green `NORMAL` in the same table; its expected value reads
  "still learning" rather than `0`.
- **Active states are `CANDIDATE`, `OPEN`, `RECOVERING`.** Derived from the
  lifecycle state only.
- **`resolved_at` is not the active-state definition.** A `NORMAL` row with no
  resolved timestamp appears under recently closed, and an active row is not
  closed by a stray timestamp.
- **TRUNCATED limits what may be claimed.** The capped dimension keeps its rows
  and states "From a result capped at the value limit — the top of the list,
  not the whole of it"; its new and disappeared counts are withheld.
- **UNAVAILABLE and FAILED remain explicit and distinct.** The dimension list
  renders `AVAILABLE`, `TRUNCATED`, `FAILED` and `UNAVAILABLE` as four named
  states, with "2 dimensions were not checked … No conclusion about them
  follows from this investigation."
- **Deterministic summaries do not claim causation.** The rendered investigation
  page contains none of "caused by", "root cause", "attacker", "compromise",
  "malicious" or "because of", and states explicitly that contributor shares are
  "a measured share of the change, not a statement of cause".
- **Contributor headline diversity is preserved.** With the three numerically
  largest contributors deliberately placed in one dimension, the headline still
  reported two dimensions — Event name and Source IP — rather than three source
  IPs.

## 4. Verification environment

- **Data: fixture, not live.** Screenshots and measurements were produced
  against a deterministic fixture API serving the backend's response shapes with
  a pinned clock. No live QRadar data appears in any committed image, and no
  QRadar request was issued during capture.
- **Fixture IDs.** Source `11111111-1111-1111-1111-111111111111`; anomaly
  `aaaaaaaa-0000-0000-0000-000000000000`. The same source id is reused for
  `/log-sources/[id]` in the shared-shell pass.
- **Query parameters.** None required. `/behavior/sources/[id]` was verified at
  its default 6-hour range; the bounded `?range=` control was exercised
  separately by the automated suite.
- **Application URL.** `http://127.0.0.1:3100` (production build), with the
  fixture API on `:8100`. Non-default ports were used so the verification stack
  could not collide with the Compose stack on `:3000`/`:8000`.
- **Browser.** Headless Chrome driven over CDP by a temporary script kept
  outside the repository. No browser dependency, CDP utility, screenshot
  framework or visual-diff infrastructure was added.

## 5. Final viewport matrix

Every principal route was checked at all three viewports; the shared-shell
routes were included at all three at negligible cost. **30 route/viewport
checks, 30 pass.**

Acceptance condition:
`document.documentElement.scrollWidth === document.documentElement.clientWidth`.

For every row below, `document.body.scrollWidth` and `document.body.clientWidth`
were equal to the document values, and computed `overflow-x` was `visible` on
both `html` and `body` — so no defect is concealed by clipping.

### Principal routes

| Route | Viewport | doc scrollWidth | doc clientWidth | Result |
|---|---|---|---|---|
| `/behavior` | 1440×900 | 1440 | 1440 | pass |
| `/behavior` | 1280×800 | 1280 | 1280 | pass |
| `/behavior` | 1024×768 | 1024 | 1024 | pass |
| `/behavior/sources` | 1440×900 | 1440 | 1440 | pass |
| `/behavior/sources` | 1280×800 | 1280 | 1280 | pass |
| `/behavior/sources` | 1024×768 | 1024 | 1024 | pass |
| `/behavior/sources/[id]` | 1440×900 | 1440 | 1440 | pass |
| `/behavior/sources/[id]` | 1280×800 | 1280 | 1280 | pass |
| `/behavior/sources/[id]` | 1024×768 | 1024 | 1024 | pass |
| `/anomalies` | 1440×900 | 1440 | 1440 | pass |
| `/anomalies` | 1280×800 | 1280 | 1280 | pass |
| `/anomalies` | 1024×768 | 1024 | 1024 | pass |
| `/anomalies/[id]` | 1440×900 | 1440 | 1440 | pass |
| `/anomalies/[id]` | 1280×800 | 1280 | 1280 | pass |
| `/anomalies/[id]` | 1024×768 | 1024 | 1024 | pass |

### Shared-shell regression routes

| Route | 1440×900 | 1280×800 | 1024×768 |
|---|---|---|---|
| `/` | pass | pass | pass |
| `/log-sources` | pass | pass | pass |
| `/log-sources/[id]` | pass | pass | pass |
| `/offenses` | pass | pass | pass |
| `/rules` | pass | pass | pass |

## 6. Local table overflow

Local scrolling inside a table's own container is the intended mechanism and is
**not** page-level overflow. Every `.table-scroll` container reported
`position: relative`, so the containment fix is intact.

| Viewport | Route | Scroll region | scrollWidth | clientWidth |
|---|---|---|---|---|
| 1280×800 | `/anomalies` | Detected anomalies | 1115 | 984 |
| 1024×768 | `/anomalies` | Detected anomalies | 1115 | 780 |
| 1024×768 | `/behavior` | Sources needing attention | 875 | 780 |
| 1024×768 | `/behavior/sources` | Monitored log sources | 857 | 780 |
| 1024×768 | `/behavior/sources/[id]` | Active anomalies | 787 | 780 |
| 1024×768 | `/behavior/sources/[id]` | Recently closed anomalies | 821 | 780 |

At 1440×900 no table needed to scroll. The anomaly contributor table fitted its
container at every viewport.

## 7. Screenshot index

Recaptured on 2026-10-15 after the UI redesign (T-111); the August captures are in git history.

Twenty-seven screenshots in [`screenshots/ui/`](screenshots/ui/). Each is a
viewport-sized capture with no browser chrome, so its pixel dimensions match its
filename. All come from deterministic fixture APIs (no live QRadar data). The top-bar
"Status Unknown/Problem" pill and the "Refreshed" clock are fixture state, not a measurement.

### Desktop reference — 1440×900

| Screenshot | What it shows |
|---|---|
| [dashboard-1440x900.png](screenshots/ui/dashboard-1440x900.png) | `/` as the SOC Overview template in read mode ("Template, not saved", no `dashboard:write`): four summary widgets (total EPS with a `Partial` badge, log-source health bar, open offenses, active anomalies), then the Fleet EPS band chart and the EPS-by-class donut |
| [behavior-1440x900.png](screenshots/ui/behavior-1440x900.png) | Behavioral overview: four counters, a lifecycle strip, the `INSUFFICIENT_DATA` notice (a zero is not a clean bill of health), and the "Needs attention" table with open and candidate anomalies |
| [behavior-sources-1440x900.png](screenshots/ui/behavior-sources-1440x900.png) | Source inventory: filter row on one line, state badges, observed against expected EPS and deviation right-aligned, and a long source name wrapping in its cell |
| [behavior-source-detail-1440x900.png](screenshots/ui/behavior-source-detail-1440x900.png) | An open source: four metrics, "Collection partial", and the 12-hour volume chart with the expected range as a band; intervals not fully collected are gaps and shaded, not zeros |
| [anomalies-1440x900.png](screenshots/ui/anomalies-1440x900.png) | Anomalies triage table across Phase A, WAF and IPS subjects; evidence status per row (Complete, Unavailable, Failed, Partial, Not requested) beside state and severity |
| [anomaly-detail-1440x900.png](screenshots/ui/anomaly-detail-1440x900.png) | A still-running volume spike: generated summary, four metrics with units (duration shows an em dash, not `0s`), detector confidence, and the timeline with the anomalous interval and lifecycle markers |
| [ips-incident-1440x900.png](screenshots/ui/ips-incident-1440x900.png) | An IPS entity incident (destination port 445/tcp rate spike): entity and source identity, detected/opened/resolved times, metrics in events / 5 min, and the timeline with a not-collected span |
| [waf-overview-1440x900.png](screenshots/ui/waf-overview-1440x900.png) | `/behavior/waf`: two WAF sources, each with profile, validation, endpoint coverage, active endpoints against the ceiling, and the admission-overflow counts |
| [waf-endpoint-incident-1440x900.png](screenshots/ui/waf-endpoint-incident-1440x900.png) | A WAF endpoint request-rate spike (`POST /api/auth/login`): metrics in requests / 5 min, endpoint coverage, and the request chart with expected band, coverage panel and midnight-crossing time axis |
| [fleet-1440x900.png](screenshots/ui/fleet-1440x900.png) | `/fleet`: review-queue banner, then per-class volume rows with expected EPS, window completeness shown as `Incomplete` with its partial-bucket counts, open incidents and top sources |
| [rules-statistics-1440x900.png](screenshots/ui/rules-statistics-1440x900.png) | `/rules/statistics` in a `PARTIAL` state (160 of 168 windows complete; 3 missing, 3 truncated, 2 failed): rule composition table and the Most matched table repeating the partial notice |
| [log-sources-1440x900.png](screenshots/ui/log-sources-1440x900.png) | `/log-sources`: six sources with criticality, status, health score and anomaly count; an unknown source shows "unknown", a dash and "never" rather than zeros, and a source in maintenance is marked |
| [admin-1440x900.png](screenshots/ui/admin-1440x900.png) | `/admin` hub: five settings areas, each with a one-line description of what it controls |

### Responsive proof — 1024×768

| Screenshot | What it proves |
|---|---|
| [dashboard-1024x768.png](screenshots/ui/dashboard-1024x768.png) | Summary widgets reflow to two columns, the header actions stay on one row beside "Widen the window to customize", and nothing overflows horizontally |
| [behavior-sources-1024x768.png](screenshots/ui/behavior-sources-1024x768.png) | Filter row stays on one line; the long source name wraps over seven lines without pushing columns out; the table holds its columns at the narrowest supported width |
| [behavior-source-detail-1024x768.png](screenshots/ui/behavior-source-detail-1024x768.png) | The four metrics reflow to a 2×2 grid and the chart stays inside its container |
| [anomalies-1024x768.png](screenshots/ui/anomalies-1024x768.png) | The triage table switches to a compact two-line row (started time and severity under the subject and detector); filters wrap |
| [anomaly-detail-1024x768.png](screenshots/ui/anomaly-detail-1024x768.png) | Metrics reflow to one wide cell plus three; a still-running incident keeps the em-dash duration |
| [ips-incident-1024x768.png](screenshots/ui/ips-incident-1024x768.png) | Header facts wrap onto their own lines and the metric block reflows without clipping |
| [waf-overview-1024x768.png](screenshots/ui/waf-overview-1024x768.png) | Source table wraps its cells (profile, overflow counts) instead of scrolling the page |
| [waf-endpoint-incident-1024x768.png](screenshots/ui/waf-endpoint-incident-1024x768.png) | Endpoint identity, summary and the reflowed metric block fit; coverage text stays in the metrics footer |
| [fleet-1024x768.png](screenshots/ui/fleet-1024x768.png) | Class rows wrap their sub-lines and the top-sources lists run onto a second line |
| [rules-statistics-1024x768.png](screenshots/ui/rules-statistics-1024x768.png) | The window selector moves below the subtitle; the `PARTIAL` notice and composition table hold their layout |
| [log-sources-1024x768.png](screenshots/ui/log-sources-1024x768.png) | Type column wraps; the status, health and anomaly badges stay content-sized |
| [admin-1024x768.png](screenshots/ui/admin-1024x768.png) | The hub list keeps its readable column width |

### Intermediate width — 1280×800

| Screenshot | What it proves |
|---|---|
| [behavior-source-detail-1280x800.png](screenshots/ui/behavior-source-detail-1280x800.png) | The four-metric row and chart hold their desktop shape at the intermediate width |
| [anomaly-detail-1280x800.png](screenshots/ui/anomaly-detail-1280x800.png) | Investigation hierarchy unchanged between 1440 and 1024 |

## 8. Frontend gates

Run from `frontend/` against the exact working tree, using the scripts defined
in `package.json`.

| Gate | Command | Result |
|---|---|---|
| Tests | `npm test -- --run` | **629 passed, 31 files** — 0 failed, 0 skipped |
| Lint | `npm run lint` | No ESLint warnings or errors |
| Types | `npm run typecheck` | `tsc --noEmit` clean |
| Build | `npm run build` | Compiled successfully; 16/16 static pages generated |

Backend tests were not run: no backend file changed in any of these workstreams.

## 9. Compose health

Command: `docker compose up -d --build`, then `docker compose ps`.

Expected seven services — six long-running plus the one-shot migration.
`qradar-mcp` is gated behind the `mcp` profile and is not part of the default
set.

| Service | State |
|---|---|
| `postgres` | Up (healthy) |
| `redis` | Up (healthy) |
| `backend` | Up (healthy) — published on `127.0.0.1:8000` |
| `celery-worker` | Up (healthy) |
| `celery-beat` | Up (healthy) |
| `frontend` | Up (healthy) — published on `127.0.0.1:3000` |
| `migrate` | Exited (0) |

- Backend `/api/v1/health/live` → `200`; `/api/v1/health/ready` → `200`
  (`{"status":"ready","database":"ok"}`).
- Frontend `/api/health` → `200`; `/behavior`, `/behavior/sources` and
  `/anomalies` each → `200`.
- Migration state accepted by the running application: Alembic upgraded to
  revision `0004`.
- Restart count `0` for every service — nothing is crash-looping.
- No task failure, error or traceback in the backend or Celery logs during the
  verification window.

No volume was removed, no database reset, no prune was run, and no QRadar write
was issued.

## 10. Known non-blocking items

- **Screenshots are fixture-based, not live.** This is deliberate — it makes the
  images reproducible and keeps live telemetry out of the repository. Live
  behavior is recorded separately in the lab documents.
- **Dependency advisories** remain outstanding and require major upgrades. They
  were deliberately not touched; `npm audit fix --force` was not run.
- **`next lint` deprecation.** Next.js reports that `next lint` is removed in
  Next 16 and suggests migrating to the ESLint CLI. The gate passes today.
- **Phase B (WAF) and later phases** are not started; the roadmap in the
  architecture document is unchanged.
- **Four commits remain unpushed** (§11).

## 11. Publication state

- Local `HEAD`: `34e056d`
- `origin/main`: `9e6bd25`
- Unpushed: `34e056d` only — the final verification commit.
- **No push was performed by this workstream**, and no commit was amended,
  rebased, squashed or force-pushed.

`origin/main` stood at `1a76dac` when this workstream began and at `9e6bd25`
when it ended. The three intervening commits — `443d509`, `6956a29` and
`9e6bd25` — were published by a push issued outside this workstream while it was
running; the ref log records it as `update by push`. The commits are unchanged
by it, and the verification recorded here was performed against exactly that
tree.
