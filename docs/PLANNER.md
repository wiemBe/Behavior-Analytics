# Planner — orchestrator log

This file is the orchestrator's record of the project: what has been done, what is in flight,
who is doing it, and what comes next. The orchestrator updates it after every delegation and
every verification. Newest log entries go first.

If this file disagrees with the code, the code wins. Fix the file.

---

## ▶ Resume here (saved 2026-10-13 ~00:40, end of the fourth session)

**Fifth session (2026-10-13, from 09:42) — if this session dies, resume from here:**
- Test DB container restarted; per-task DBs recreated as needed (`createdb -U test …`).
- **Merged into `feat/phase-ce` this session:** T-047 (`dafe5ae`), T-024 (`400f9d1`), T-056
  (`e5b75f7`), **T-042** (`a365f15`, migration head now `0015`).
- **In flight:**
  - **T-046b** (opencode `big-pickle`, worktree `t046`, merge of `feat/phase-ce` in progress there,
    no conflicts): re-chain `0016` after `0015`, migration tests, round trip. Then commit + merge.
  - **T-048** round 1 (opencode `nemotron-3-ultra-free`, worktree `t048`): `STOP` cap fix.
  - **T-057** WAF analyst API (Sonnet, worktree `t057`).
- **Next:** T-049 (Codex) once T-046 is merged (migration `0017`); T-045 docs (free model; also fix
  the stale `expected_eps` comment in `backend/app/schemas/fleet.py`); full gate; merge to `main`.
- **User question (2026-10-13):** "did you add a way to see the custom properties and say 'in WAF
  this one is X'?" Answered: yes (T-043 catalog + T-044 editor + T-042 API). Offered an optional
  auto-suggestion (name- and value-based) — awaiting the user's answer.

**The user stopped the session with "hiçbir şey yapma sadece son durumu planner md ye kaydet".
Nothing is running.** No agent was left mid-task except T-024 (below), which died on the Claude
session limit.

**Read §0 first.** The user set three product goals (G1 log-source fleet, G2 IPS/WAF behavior,
G3 rule analytics). Every task is scheduled against them.

**Working model:**
- The orchestrator writes **no code**, not even probe scripts.
- Each round uses Sonnet (medium), Codex **and** the opencode free models.
- UI work must clear AGENTS.md §3a ("no AI slop"), and the orchestrator reviews its screenshots.
- **Run `codex exec` and `opencode run` with `< /dev/null`**: without it they can hang on stdin.
- **Agents stop on test conflicts.** When an existing test asserts behavior a spec deliberately
  changes, the agent stops and reports it. The orchestrator rules: prefer changing setup only;
  prefer changing the code if that keeps the old behavior; update a pinned value only when the
  spec changes it on purpose. Every ruling is recorded in the task rows of §3.
- **The user, on 2026-10-12: "hangisi iyiyse onu kullan bana takılma".** Pick the free models
  yourself. **The user, on 2026-10-12 (fourth session): "kalan işlere de devam et … ajanlarını
  kullanmayı unutma"** — run several threads in parallel, always through agents.

**Branches:**
- **`main` @ `b4539c6`** (2026-10-13): `feat/phase-ce` @ `8e0187e` merged with `--no-ff` after a
  green full gate on that commit — backend **2,765 passed**, 5 skipped, 0 failed (fresh DB);
  ruff clean; mypy 31 errors in 18 files (baseline); alembic 0012→0014→0012→0014 clean, no
  drift; frontend lint, typecheck, build, **796/796** — and T-040's live validation.
- **`feat/phase-ce` @ `45986cd`** (pushed; the main checkout is on it). On top of `main` it adds
  T-044 (`4938c9b`, the WAF mapping UI) and docs only (T-030 report, Phase D design, planner).
  Migration head: **`0014`**. Not gated as a whole since `8e0187e`; T-044 was gated on its own.
- **Worktrees** (`../QRadar-Dash-wt/`):
  - **still needed:** `t042` (T-042, review fixes owed), `t046` (T-046, merge owed), `t024`
    (T-024, partial work, uncommitted);
  - **merged, removable:** `t022c`, `t023`, `t031`, `t040`, `t043`, `t044`, and `gate` (detached,
    used for the full gate and the `main` merge). `t021`…`t039` were removed this session.
- Per-task test DBs on `:5433`: `qradar_obs_t022c`, `t031`, `t040`, `t042`, `t043`, `t023`,
  `t046`, `gate`, `gate_mig`. The test-DB container is tmpfs; after a restart, recreate the ones
  still needed (`t042`, `t046`) with `createdb`.

**State by thread (fourth session):**

| Thread | State | What the next session does |
|---|---|---|
| **D-8 field mappings in the UI** (the user's request this session; design [`FIELD-MAPPING.md`](FIELD-MAPPING.md)) | T-041 research DONE. **T-043** catalog backend MERGED (`dadbe0d`). **T-044** UI MERGED (`4938c9b`) after two screenshot reviews. **T-042** backend (Codex) FINISHED but **not merged**: gates green in its worktree (ruff clean, mypy 31, 2,081 unit, 274 integration, migration `0015` round trip and `alembic check`), worker race handled correctly (`FOR UPDATE` re-read, stale result discarded and re-enqueued). | **1. Resume Codex on T-042** (session `01a131e3-49cc-7e32-8a85-a6975a548d1d`, run from `../QRadar-Dash-wt/t042`, `codex exec resume … < /dev/null`; the updated `FIELD-MAPPING.md` is already copied into the worktree) with these review fixes: **(a)** the §FM.4 "Contract additions" it started before: `min_coverage_ratio` in `MappingSchemaOut`; `validation_pending` / `validation_requested_at` / `validation_requested_by` on `GET /waf/sources` items; `instance_id` on candidates; every 409 `detail` = `{message, current_version, updated_by, pending_since}`; a `WafProfileError` 422 `detail` = `{message}` — T-044's UI already reads these shapes; **(b)** the worker re-enqueues **immediately** when the source's pipeline lock is held → a hot loop of Celery tasks for as long as a collection runs: re-enqueue with a countdown (≈30 s) and a bounded number of lock retries, then record "source busy" and clear pending; test it; **(c)** `app/main.py` CORS `allow_methods` lacks `PUT`, so the dev SPA on :3000 cannot call any PUT cross-origin (also breaks T-038's `/admin/search-cadence` PUT in dev): add `PUT`, with a preflight test. Accepted as is: `collection_ineligibility` now checks the version before the status, and the one pinned reason message in `test_waf_productionization.py` changed accordingly (the spec required the version-specific reason; fail-closed unchanged). **2.** Re-run its gates, merge it (`0015`). **3.** Then **T-046**: re-chain `0016_rule_capacity_sample` `down_revision` from `0014` to `0015`, run 0014→0016→0014→0016 + `alembic check`, merge. **4.** **T-045** docs (opencode free model): README admin section; WAF design §27.2 rule 6 and §27.14 point to `FIELD-MAPPING.md`. |
| **Phase D — IPS** (G2) | **Designed:** [`PHASE-D-IPS-BEHAVIOR.md`](PHASE-D-IPS-BEHAVIOR.md) (D-8 IPS profile with normalized-or-custom references and FTD/PAN presets; fleet-wide keyed search per dimension per tick, split by time then source; 7 detectors; task split T-049–T-055). **T-030 DONE:** both lab IPS sources auto-discovered (PAN id 412 type 206, FTD id 413 type 448); IPs, port, protocol 100 % normalized; signature via QID; **no action field on either product**. | **T-049** (IPS profile + `/ips` admin API, Codex) after T-042 merges, since it reuses T-042's async-validation and API patterns. Then T-050 → T-051 → T-052; T-053 UI after T-049. |
| **T-024** fleet UI + shell fixes (Sonnet) | **Died on the Claude session limit** ("resets 4:30am Europe/Istanbul"), after writing most of the code; its last words were "Now component and page tests". Partial, uncommitted work in `t024`: `app/fleet/**`, `app/admin/classification-rules/**`, `components/fleet/**`, `lib/fleet.ts` (+ tests), edits to `api.ts`, `nav.ts`, `nav.test.ts`, `globals.css`, `admin/page.tsx`, **and to `app/behavior/page.tsx` + `page.test.tsx`** (the overview fixes — check that existing test edit is not a weakening). No report, no screenshots. | Relaunch a Sonnet agent on the same worktree with the [spec](tasks/T-024.md) plus "review what you inherit, finish the tests, capture the screenshots". Then the orchestrator reviews the screenshots (§3a) and merges. Merge after T-044 is already in: expect small additive conflicts in `api.ts`, `nav.ts`, `nav.test.ts`, `globals.css`, `admin/page.tsx`. |
| T-023 fleet API (Sonnet) | MERGED (`8b27ca9`) after one review round (expected EPS over every spanned cell). | — (open point: WAF anomaly alerts carry no `source_class`). |
| T-046 rule statistics (Sonnet) | Accepted; **merge owed** (see D-8 row, step 3). | — |
| T-040, T-022c, T-031 | DONE and merged (see §3). | — |
| Search cadence (D-7) | On `main`. Live-validated (§SC.10). | **T-048** (low): pad the Ariel `STOP` by the settle delay (T-040 Finding 1). Phase A split-on-failure stays open until a busy console is measured. |

**Next steps, in order:**
1. T-042 review fixes (Codex resume), gates, merge; then T-046 re-chain and merge.
2. Relaunch T-024 (Sonnet, after 04:30 Istanbul), review screenshots, merge.
3. T-045 docs (free model). Full gate on `feat/phase-ce`; merge to `main`.
4. Phase D: T-049 (Codex) → T-050 → T-051 → T-052; T-053 (UI); T-054 live; T-055 docs.
5. Later: T-048; E.3 rule-statistics UI (D-5 still the user's call); the WAF analyst API and UI
   (with the IPS analyst view, §D.11); Phase C.2 (per-class outage policy, SIM Generic); Phase 4
   OIDC (**no authenticated route works in production until OIDC lands**).

**Known open points (small):**
- **Threshold validation gap (T-033).** A `GT` threshold set above a search's `max_result_rows`
  can never fire on a truncated run.
- **"No verdict" storage (T-033).** It is stored as `threshold_breached=False`. The alert evaluator
  tells it apart through `truncated`; a three-state column would need a migration.
- `RunSearchButton.test.tsx` flaked once under load during T-038. It passed on every re-run.
- **WAF dispatch counts.** The dispatcher records its runs with `searches_sent = null` ("not
  counted"), because per-source tasks run asynchronously and are not aggregated.
- **`STOP` edge effect (T-040).** The last bucket of an Ariel span can miss events stored just
  after `STOP` (5 events in 2.4M, live). Follow-up T-048.
- **WAF anomaly alerts carry no `source_class`** (T-023), so class routing does not reach them.
- **Dev CORS lacks `PUT`** (`app/main.py`): PUT calls from the dev SPA on :3000 fail (search
  cadence, WAF profile). Folded into the T-042 review fixes.
- `RunSearchButton.test.tsx` flaked again once in a parallel run (T-044); passes alone.

**Environment notes:**
- The test DB container `qradar-observability-test-test-db-1` runs on `:5433`, on tmpfs. Restart
  it with `docker compose -f docker-compose.test.yml up -d test-db`, then `createdb` per task.
- The lab QRadar is reachable. For host-side read-only runs, override `QRADAR_API_TOKEN_FILE` and
  `QRADAR_CA_BUNDLE` to `.secrets/` in the process environment only.
- **Never call `GET /analytics/rules/{id}/dependents`:** it starts a server-side task.
- **Codex.**
  - It hit its usage limit at 19:28 on 2026-10-12, after about three tasks (T-034, its fix, and
    most of T-035). It resets at 23:41.
  - Fourth session: T-042 ran 40 min (≈607k tokens) with no usage-limit hit. Its session id is
    `01a131e3-49cc-7e32-8a85-a6975a548d1d` (for the review-fix resume).
  - Give it one large piece per window. Check every log for "usage limit".
  - For review fixes, continue the same session with `codex exec resume <session-id> -c sandbox_mode='"workspace-write"' -c sandbox_workspace_write.network_access=true -o <file> "<msg>" < /dev/null`,
    run from the worktree.
- **opencode** is v2.0.27. The user re-authenticated it after the "Invalid credential" failure.
  `opencode models` lists models again without a TTY.
  - Verified on 2026-10-12 with a file-reading tool test:
    - **`opencode/`:** `big-pickle`, `space-bunny-free`, `muse-spark-1.3-contributor-free`,
      `nemotron-3-ultra-free`, `longcat-2.5-preview-free`, `mimo-v2.6-flash-free`,
      `nemotron-3.5-lightning-free`, `ling-3.1-flash-free`.
    - **`openrouter/…:free`:** `thinkingmachines/inkling(-small)`,
      `nvidia/nemotron-3-super-120b-a12b`, `nvidia/nemotron-3-ultra-550b-a55b`,
      `nvidia/nemotron-3.5-lightning`, `cohere/north-mini-code`, `apodex/apodex-1.1-mini`,
      `dots-studio/dots-3-note-preview`, `poolside/laguna-xs-2.1`.
  - Avoid:
    - `poolside/laguna-s-2.1` and `nemotron-3-nano-omni`, which gave wrong answers;
    - both `gemma-4` models, which return provider errors;
    - `exo-free` (deprecated) and `ling-3.0-flash-fin-free` (unavailable).
  - This session, `big-pickle` (a migration-test pin) and `mimo` (T-039 docs) did real work
    cleanly.
- GLM credits return on 2026-10-14.
- **Claude (Sonnet sub-agents) hit the session limit at ~00:35 on 2026-10-13**, resetting at
  04:30 Europe/Istanbul. T-024 died on it mid-task.
- This session `mimo` (T-022c) and `big-pickle` (T-031) both delivered clean, reviewable work.

---

## 0. Product goals (set by the user on 2026-10-12)

Every phase and task is scheduled against these three goals. When a piece of work serves none of
them, question it before starting it.

### G1 — Log-source fleet monitoring

The user's goal, in summary:
- detect a log source whose flow departs from its normal pattern;
- raise alerts when selected sources stop sending (outage);
- see which sources consume more EPS than normal;
- detect newly arrived sources and classify them;
  - sources are mostly named after what they are (`waf`, `ips`, `linux`, `windows`), so a name
    regex can classify them;
  - pulling QRadar's type and group would classify them more reliably;
- track SIM Generic Log as well.

| Need | What exists | Gap |
|---|---|---|
| Flow departs from normal | Phase A: seasonal weekday×hour baselines; `VOLUME_SPIKE`, `VOLUME_DROP`, `NO_EVENTS`; lifecycle; alerts. Live-validated 2026-08-02. | — |
| Outage alerts for selected sources | `NO_EVENTS` raises alerts. Routing matches severity, owner, instance and alert type. Per-source `criticality`, `monitoring_enabled` and maintenance exist. | No policy for "alert on outage only for these sources or classes". Routing cannot match a source class. |
| EPS above normal | `VOLUME_SPIKE`. `log_source_metric` holds `average_eps` and `peak_eps`. | No fleet EPS view: per class, top consumers, EPS against baseline. |
| New sources | Inventory sync creates rows silently. | No `first_seen_at` on `log_source`, no review queue, no new-source alert. |
| Classification | `type_id` and `type_name` (resolved through `log_source_types`). | No class field. QRadar log-source groups (`group_ids`, `/log_source_groups`) are not fetched. No name-regex rules. |
| SIM Generic Log | Nothing. | Volume tracking for SIM Generic / unknown events, and a bounded list of the senders feeding it (candidate unconfigured devices). |

The planned order of precedence for classification is:
operator override > QRadar group mapping > QRadar type mapping > name regex > `UNCLASSIFIED`.
The class that applied and the rule that matched are recorded with it.

### G2 — IPS and WAF traffic behavior

The goal: detect whether the ports, IPs and endpoints seen on the IPS and WAF arrive at their
normal rates, and alert on a deviation.
- **IPS:** track ports and IPs.
- **WAF:** the same, plus URI and HTTP status.

| Need | What exists | Gap |
|---|---|---|
| WAF URI rate | `WAF_REQUEST_RATE_SPIKE` per normalized endpoint. Live-validated 2026-08-03. | — |
| WAF HTTP status | `WAF_ERROR_RATIO_SPIKE` (5xx) and `WAF_CLIENT_ERROR_RATIO_SPIKE` (4xx). | Phase B.2 is finishing on `feat/phase-b2`. |
| WAF IPs | `WAF_CLIENT_CONCENTRATION_SPIKE`. | Phase B.2, same branch. |
| WAF new URIs | `WAF_NEW_ENDPOINT_ACCESS`. | T-006b, in review. |
| WAF alerting | WAF incidents have a lifecycle. | **WAF incidents raise no alerts at all** (`app/waf` never calls `AlertService`). There is no analyst API and no UI. |
| IPS port and IP behavior | Nothing. Phase A volume detectors run on IPS sources as on any source. | A whole phase: per-source destination-port and IP behavior, built on the WAF architecture (profiles, validated field mapping, bounded breakdowns, fail-closed). Design first. |
| Real products | Lab-only validation. | A field-availability pass against the production WAF and IPS products. |

**Production IPS products (user, 2026-10-12): Cisco Firepower/FTD and Palo Alto (THREAT logs).**
Phase D's profiles target these first. The user asked the orchestrator to create the lab IPS
sources. Because the platform is read-only and the lab guide forbids API creation, they are
created through QRadar auto-discovery from vendor-native synthetic syslog (T-029, T-030).

### G3 — Rule analytics

The goal: see how much each rule fires, how much EPS it consumes, and how many rules are active
or passive. Collect every rule statistic available and analyze it. Later, give the statistics and
each rule's context to the internal AI for rule analysis. The questions to ask the AI are to be
decided later.

| Need | What exists | Gap |
|---|---|---|
| Active / passive counts | Rule and building-block inventory (`enabled`, `origin`, `type`). Live-validated: 352 rows. `/rules/health-summary`. | No statistics view: counts by type, origin, building block and state. Unknown whether the UI shows them. |
| EPS cost | `/analytics/rules` on 7.6 returns `average_capacity`, `base_capacity`, `base_host_id` and `capacity_timestamp` (PHASE3-HANDOFF §11.2). Only `average_capacity` is stored, and each sync overwrites it. | Store all four as a time series. Rank the most expensive rules and show the trend. |
| Firing counts | Inferred lower bounds from offense contributions (`rule_metric_collector`). Rule health is mostly `INSUFFICIENT_DATA`. | A measured source: an Ariel aggregate over `creEventList`, bounded and one search per interval. **The AQL must be validated on live QRadar first.** |
| AI analysis | `LLMProvider` exists as an interface, disabled by default. | A rule-context package: metadata, statistics and logic. The rule logic (tests) is **not** in the 7.6 REST payload, so where it comes from is an open question (D-5). |

### Roadmap, in order

1. **Finish Phase B.2** (G2, WAF): T-006b, T-009, full gates, D-3, T-010, then merge to `main`.
   It is nearly done, and every later phase adds migrations, which must chain after 0010.
2. **Phase C.1, source inventory and classification** (G1). Groups, `first_seen_at`, the class
   with its provenance, the regex rules, a new-source queue and alert, and an EPS-by-class view.
   **Phase E.1, rule statistics and cost** (G3). Capacity history, active/passive statistics and
   a top-cost list. The two are independent and can run in parallel.
3. **WAF alerting** (G2): route WAF incidents through `AlertService`. Then the analyst API and UI
   (design §27.14–27.15).
   Before any new UI: a **UI quality pass** on the existing behavioral routes against §3a. Known
   defects:
   - a marker on every chart point;
   - the expected range drawn as three dashed lines instead of a band;
   - a row of identical KPI cards on the incident page;
   - oversized wrapping titles.

   New screens then start from a corrected base.
4. **Phase D, IPS behavior** (G2): design document first, then build.
5. **Phase C.2** (G1): an outage-alert policy per class and per source, and SIM Generic Log
   tracking. **Phase E.2** (G3): measured firing counts from `creEventList`. Both need live
   QRadar validation.
6. **Phase E.3** (G3): the AI rule-analysis package, once D-5 is settled.

---

## 1. How this project is run

**Orchestrator:** Claude Opus 5.5, xhigh effort. It plans, splits the work into tasks, delegates
them, reviews every result, runs the gates, and keeps this file current. It does not write feature
code itself.

**Coding agents:**

| Agent | How it is invoked | Availability | Good for |
|---|---|---|---|
| Sonnet 5.5, medium effort | Claude Code Agent tool | Available | Precise changes inside existing structure; tests; docs |
| Codex (ChatGPT, gpt-5.6-sol) | `codex exec -C <dir> -s workspace-write -o <file> "<prompt>"`. The binary ships inside the VS Code ChatGPT extension and is not on PATH. | Available | Larger self-contained pieces |
| GLM 5.3 / GLM 5.3 Flash | `opencode run -m zai-coding-plan/glm-5.3[-flash] --auto "<msg>"` | **Unavailable until 2026-10-14** (credits) | — |
| opencode free models | `opencode run -m <id> --auto "<msg>"` | Available, **model IDs not yet known** | Mechanical edits |

**Rules for every delegation:**

1. Each delegation is a task on the board (§3) with an ID, an owner and acceptance criteria.
2. An agent gets one narrow task, the files it may touch, and the gates it must pass.
3. A task becomes `DONE` only after the orchestrator has reviewed the diff and run the gates
   itself. An agent saying "done" is not enough.
4. Agents never commit. The orchestrator commits and pushes. The user granted standing
   permission on 2026-10-10, except for hard blocks: force-push, deleting unbacked work,
   SELinux, secrets, or red gates landing on `main`.
5. Shared rules for all agents are in [`AGENTS.md`](../AGENTS.md).

**Isolation:** each agent works in its own git worktree.
- Location: `../QRadar-Dash-wt/<task-id>`, on branch `agent/<task-id>`, cut from the integration
  branch.
- The worktree reuses the main checkout's `backend/.venv` through a symlink. Every command needs
  `PYTHONPATH=.`; without it the editable install imports the main checkout's `app`.
- Each agent gets its own test database, `qradar_obs_<task-id>`, on the test server at
  `localhost:5433`. Never run two pytest processes against one database.
- The orchestrator reviews the worktree diff, runs the gates there, then merges the branch into
  the integration branch.

**Branches:**
- `main` stays green.
- Phase B.2 is integrated on `feat/phase-b2`. It merges to `main` once every gate passes.

**Gates:**

- `make lint`
- `make test` (unit)
- `make test-integration`
- `make check` (migration drift)
- `mypy app`: advisory. The baseline is 31 errors; do not regress it.
- Frontend: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`
- **UI tasks, additionally:** screenshots at 1440×900, 1280×800 and 1024×768, captured against a
  fixture API and **reviewed by the orchestrator** against `AGENTS.md` §3a, the frontend quality
  bar the user set on 2026-10-12 ("no AI slop"). Green tests alone do not accept a UI task. UI
  work goes to Sonnet or Codex, never to the free models.

---

## 2. Current state (2026-10-10)

**Last commit:** `6f17a73` on 2026-08-03. This is the Phase B first vertical slice, live-validated.

| Work | State |
|---|---|
| Phases 1–3, Phase A | Committed, complete, live-validated |
| Phase B first slice (`WAF_REQUEST_RATE_SPIKE`) | Committed, live-validated 2026-08-03 |
| Phase B.1: collection productionization (migrations 0006–0008) | Complete per [design §28](PHASE-B-WAF-BEHAVIOR.md), **not committed** |
| Phase B.1.5: resilience and scale (migration 0009) | Complete per [design §29](PHASE-B-WAF-BEHAVIOR.md), **not committed** |
| Phase B.2: behavioral detectors (migration 0010) | **Half-built, undocumented, not committed.** See below |

The uncommitted change set spans 35 modified files (+5,341 / −703 lines) and about 33 new files.

**Phase B.2 work in progress, as found:**

| Piece | Present? |
|---|---|
| Migration `0010_waf_behavior_breakdowns` (action map, per-breakdown measurement, ratio sums) | Yes |
| `app/waf/breakdown.py`: fold breakdown searches into per-endpoint counts | Yes, untested |
| Provider `get_waf_breakdown` (base / mock / REST) and collector breakdown searches | Yes, untested |
| Capability specs for the five new detector types | Yes |
| `app/waf/behavior.py`: block / server-error / client-error ratio, client concentration, new-endpoint-access detectors | Written, untested, **not called by the engine** |
| Ratio baseline training (`numerator_sum` / `denominator_sum` written by `app/waf/baseline.py`) | **Missing.** The columns exist and the detectors read them, but nothing writes them. |
| Engine wiring (`app/waf/engine.py` registers only `WAF_REQUEST_RATE_SPIKE`) | **Missing** |
| `WAF_CLIENT_ENDPOINT_FANOUT_SPIKE` | Named only, so that its blocker is published. It has no detector function, by design. |
| Design doc section and README entry | **Missing** |

**Gates measured on 2026-10-10, uncommitted tree:**

- Unit: 1,166 tests, **6 failed**. All six are in `tests/unit/test_detector_capabilities.py::test_status_matrix`, where `EXPECTED_STATUS` has no entry for the B.2 types (`KeyError`).
- Ruff: **7 × E501** — six in `app/waf/behavior.py`, one in `app/cli/waf.py`.
- Not run since 2026-09-16: integration tests, `alembic check`, mypy, frontend gates.

---

## 3. Task board

Statuses: `TODO`, `IN PROGRESS`, `REVIEW` (agent finished; orchestrator verifying), `DONE`,
`BLOCKED`.

| ID | Task | Owner | Status | Acceptance |
|---|---|---|---|---|
| T-001 | Get the uncommitted work under version control and pushed | Orchestrator | DONE (changed scope; see the 2026-10-10 log) | Work pushed; `main` unchanged and green |
| T-002 | Write `AGENTS.md`: the shared rules every agent follows | Orchestrator | DONE | Codex and opencode pick it up; Sonnet prompts reference it |
| T-003 | Re-establish the full gate baseline on `feat/phase-b2`: integration tests on the test DB, `make check`, mypy, frontend gates | Orchestrator | DONE | Actual output of each gate recorded in the log |
| T-004 | B.2: fix the 7 ruff E501 errors | opencode `mimo-v2.6-flash-free` | DONE (`be79d8b`) | `make lint` clean; no behavior change in the diff |
| T-005 | B.2: train the ratio and share baselines (`block_ratio`, `server_error_ratio`, `client_error_ratio`, `top1_client_share`) | Codex | DONE (`53385f0`) | Pooled sums and every exclusion tested; statement count unchanged; registry guard test |
| T-006a | B.2: run the ratio and concentration detectors in the WAF engine | Codex | DONE (`b52a03d`) | Incidents open and persist; NOT_COLLECTED → INSUFFICIENT_DATA; settings off → engine unchanged; constant statements with 5 detectors per endpoint |
| T-006b | B.2: novelty (bulk `NoveltyFacts`/`HistoryFacts`, `detect_new_endpoint_access` wired); replace the production `assert` from T-006a; fix the T-006a mypy error at `engine.py:443` | Codex (cut off by its usage limit) → Sonnet finishing | DONE (`26fe0d9`) | History completeness proven per bucket; flood at appearance → INSUFFICIENT_DATA; constant statements; no mypy error in `engine.py` |
| T-007 | B.2: tests for `breakdown.py`, collector breakdown searches, and `get_waf_breakdown` (mock and REST) | Sonnet | DONE (`c122b42`) | FAILED, NOT_COLLECTED and truncated breakdowns are covered; unmapped action values never count as allowed |
| T-008 | B.2: unit tests for every `behavior.py` detector guard | Sonnet | DONE (`e4fcaf5`) | Every INSUFFICIENT_DATA / HEALTHY / anomalous exit is pinned, including INCREASE_ONLY scope |
| T-009 | B.2: capability truth (spec written; launches after T-006b). Set `EXPECTED_STATUS` and the published-detector API test for the new types (fixes 7 failing tests), and add a guard that every OPERATIONAL WAF spec has a registered engine function. | Sonnet | DONE (`977adf0`) | Statuses match what actually runs; the registry can no longer claim a detector the engine does not run |
| T-010 | B.2: design doc §30 and README roadmap entry | Sonnet | TODO (last) | Documents what was built, measured and deferred, in the existing doc style |
| T-011 | Migration tests vs 0010: pin the 0009 test to revision `0009`; add a 0010 round-trip test under the transient-default convention | Sonnet | DONE | `test_migrations.py` 10/10 (re-run by the orchestrator on a fresh DB) |
| T-012 | mypy regression at `waf_endpoint_collector.py:572` (type-only) | opencode `ling-3.1-flash-free` | DONE (`75b2f1e`) | That error gone; ruff clean |
| T-013 | Breakdown fold defects found by T-007: non-ASCII status digits; unparseable source IPs posing as a client | Sonnet | DONE (`02374e9`) | ASCII-only status codes; unparseable values unattributed; regression tests; one defect-pinning test replaced with approval |
| T-014 | **Production wiring gap:** the scheduled pipeline never passed a `BreakdownPlan` to the collector | Codex → Sonnet | DONE (`8e00bbb`) | 4 tests through `run_waf_source`, including a block-ratio incident opening through the scheduled path alone; 127 WAF integration tests re-run green |
| T-015 | G1/G3 research: live discovery on the lab QRadar | Sonnet | DONE (`e93354c`, [report](tasks/T-015.report.md)) | 11 questions answered read-only; 7 of 12 Ariel searches. Side effect noted: `GET /analytics/rules/{id}/dependents` starts a server-side task. It is banned from now on. |
| T-016 | G2: raise alerts from WAF incidents (`waf_anomaly` fingerprint, `waf_alerting_enabled` shadow-mode switch, enqueuer wiring) | Codex | DONE (`7f20f8e`; full suite 2,085 passed) | 4 statements with no transitions and 8 on refresh, both at 40 and 800 endpoints. Codex found a latent alert-severity defect and worked around it locally, without reporting it → T-019. |
| T-017 | B.2: decision D-3. Ratio and concentration training and detection require source-level coverage | Sonnet | DONE (`ab006aa`; re-run by the orchestrator: 1,626 passed) | New `low_source_coverage` tally; INSUFFICIENT_DATA below the floor; at-floor still judged; no assertion changed |
| T-018 | Test hygiene: drop the redundant module-level asyncio mark in `test_waf_collection.py` (5 PytestWarnings) | opencode `mimo-v2.6-flash-free` | DONE (re-run by the orchestrator: 23 passed, 0 warnings) | Same pass count, warnings gone |
| T-019 | Defect: `Alert.severity` loads as `str`, so `_render` (`.value`) crashes on any DB-loaded alert when a route matches. This hits Phase A recovery and the API acknowledge/resolve paths. Reproduce first, fix in the dispatcher, drop T-016's workaround | Codex | DONE (`6d860e8`; reproduced 3 failures first; re-run by the orchestrator: 41 passed) | Failing regression tests before the fix; green after; audit list of similar `.value` reads |
| T-010 | (re-listed) B.2 design doc §30 and README | Sonnet | DONE (`9951470`, with one follow-up round) | §30.1–§30.14; README status, roadmap, data model, test counts |
| T-020 | Replace the stale `docs/NEXT-SESSION-PROMPT.md` with a pointer to the planner | opencode `ling-3.1-flash-free` | DONE (verified: exact text, one file) | Exact text; one file |
| T-021 | **UI quality pass** on the behavioral routes, against AGENTS.md §3a (the user's "no AI slop" bar) | Sonnet | DONE (`526e0d0`; screenshots reviewed by the orchestrator; 695 frontend tests, lint, typecheck and build re-run) | Clean chart (band, units, UTC), one metrics block, UTC everywhere, states designed. Left for T-024: the overview table's severity column has no header; two overview stat cards are uneven; the sidebar background stops at the viewport height on long pages. |
| T-022 | **Phase C.1 (G1):** inventory enrichment (groups, types, facts), migration `0011`, `classify()` with seed rules, new-source detection and alert ([design](PHASE-C-LOG-SOURCE-FLEET.md)) | Codex | IN PROGRESS (`agent/t022`) | Full gates; constant SQL per sync; the seed list reviewed by the orchestrator |
| T-025 | **Phase E.1a (G3):** measured rule matches from `creEventList`, migration `0012` (temporarily chained to `0010`; re-chain at merge), rule-health integration ([design](PHASE-E-RULE-ANALYTICS.md)) | Sonnet | DONE, merged into `feat/phase-ce` (`e77cb99` + re-chain `0d037f5`) | Full gates; EVENT rules provably silent only over complete windows; COMMON never silent |
| T-026 | Record the T-021 contracts (`IncidentMetrics`, `LoadingState`/`ErrorState`, UTC, the severity tone) in the UX guidelines | opencode `mimo-v2.6-flash-free` (`longcat` hung) | DONE (verified: additions only, placed correctly) | Three verbatim insertions; one file |
| T-022b | Follow-ups on T-022: the seed overlap (Azure AD matched CLOUD and IDENTITY at equal priority); built-in rule updates must reach existing installs (upsert definitions, keep `enabled`); pin the upgrade path (pre-0011 sources become REVIEWED, no alert flood) | Codex | IN PROGRESS (`agent/t022`) | Guard test over ≥ 40 realistic DSM names; upsert test; upgrade test |
| T-025b | Follow-up on T-025, liveness: a permanently failing window stalled the watermark forever. Bounded attempts, then skip as FAILED; a catch-up horizon with a bulk skip | Sonnet | DONE (diff read by the orchestrator; merged with T-025) | Skip after N attempts; transient failures not skipped; constant-cost bulk skip; health stays fail-closed over skipped spans |
| T-027 | G2 research: live IPS field discovery for the Phase D design | Sonnet | DONE ([report](tasks/T-027.report.md)) | **The lab has no live IPS telemetry**: 12 Snort/McAfee sources have been silent for about 71 days, and the Phase A IPS source does not exist. Ariel syntax facts: `SUM(CASE…)` is rejected, absent ports are 0 (not NULL), `UNIQUECOUNT` counts 0. Phase D validation needs a lab IPS source created by the user and a generator extension. |
| T-032 | **Search cadence research (D-7):** live, read-only. Can one Ariel search return every 5-minute bucket of a cadence window? Covers a timezone-free bucket key, equivalence with today's per-bucket query, cost at 1 h and 8 h, `LIMIT` across buckets, `creEventList` by bucket, and the settle delay | Sonnet | DONE ([report](tasks/T-032.report.md); verified against the raw outputs) | ≤ 12 searches; every question answered with exact AQL; the design implications |
| T-033 | **Defect:** `get_ariel_search_results` can never report truncation, because the `Range` header caps rows before the check. As a result, scheduled searches record capped results as complete and apply thresholds to them, and Phase A would zero-fill dropped sources. Reproduce first; fail closed | Sonnet | DONE (`6c225f9`, cherry-picked from `agent/t033`). Reproduced with 4 unit and 8 integration failures. The orchestrator read the diff and re-ran 110 unit and 43 integration tests green. It approved one existing-test edit: the `Range` header pin `items=0-3`→`items=0-4`, the new required value, with no weakening. Open: a GT threshold above `max_result_rows` can never fire on a truncated run (search-config validation gap); "no verdict" is stored as `threshold_breached=False`, and the alert evaluator tells it apart through `truncated`. | Failing tests first; no total metric and no resolve on truncated results; Phase A raises; caller audit |
| T-034 | Search cadence core: `search_cadence` (0013), slot claim, API and audit; gates `ANOMALY_EXPLANATION` | Codex | DONE (`5d6cf4a`). The orchestrator found that `next_dispatch_at` was off the new grid after a cadence change. Codex fixed it (resumed session) with an all-pairs property test. The 0012 migration test was pinned to its revision by opencode `big-pickle`. The orchestrator re-ran 135 unit and 25 integration tests; alembic 0012↔0013 is clean with no drift. | One winner per slot (concurrency test); 401/403/404/422; audit on change |
| T-035 | Phase A: one keyed search per tick, split on truncation, evaluation watermark (0014), Phase A gate | Codex → Sonnet | IN PROGRESS (`agent/t035`). Codex first stopped correctly on a test conflict: `test_bounded_backfill` asserts ≤ 4 buckets. The orchestrator ruled: keep the assertion, pin that test's cadence to 5 min (where the new bound 1+3 equals the old one), and add a new 1-hour test expecting 15 buckets. Codex then hit its usage limit after about 900 lines (reset 23:41). The work was handed to Sonnet with "review what you inherit". **DONE** (`0f7a014`). Sonnet kept the inherited work and fixed three gaps: a lone-bucket failure rolled back the earlier complete buckets; two evaluation handlers swallowed errors silently; a crashed run was never recorded. The orchestrator reviewed the collector, `evaluate_pending`, the gate, the REST AQL and the equivalence test (24 buckets, silent source, every column). It re-ran 1,971 unit and 42 integration tests; alembic 0013↔0014 is clean with no drift. | Equivalence with per-bucket rows, zero-fill included; every new bucket judged in order |
| T-036 | WAF: keyed searches and the WAF gate ([spec](tasks/T-036.md)) | Sonnet (Codex was out until 23:41) | DONE (`c5c61a4`). Two rounds of orchestrator rulings on 4 conflicting tests: the beat pin went to 60 s; the per-bucket permit test was rewritten to "a tick without a permit collects nothing"; a stub gained the keyed method; an evaluation-bound test was pinned to 5 min with its assertions kept. The orchestrator also found a real defect: the WAF evaluation cap (24) was below what one long-cadence tick persists, so the backlog would grow every tick. The bound is now raised to the cadence window when a run spans several buckets. The orchestrator re-ran 2,067 unit and 93 WAF integration tests. Per-tick cost with 3 breakdowns: 5 Ariel searches instead of 60, and DB statements are 6 per bucket plus 6. | §SC.9 |
| T-037 | Rule matches: one keyed search per tick, windows per run sized to the cadence, rule-match gate | Sonnet | DONE (`agent/t037` @ `6bd5881`, awaiting cherry-pick after the T-035 gate). The agent stopped on 5 conflicting tests. The orchestrator ruled on them: 3 kept unchanged by changing the formula to `ceil(cadence/window) + max_windows_per_run - 1`, which keeps today's bound when the cadence is no longer than the window; the beat pin updated to 60 s; one `attempts` pin updated from 2 to 1 under split-first, with the transient-failure intent kept. The orchestrator re-ran 1,991 unit and 49 integration tests. | Equivalence with per-window rows; every T-025/T-025b guarantee kept |
| T-038 | UI: `/admin/search-cadence`, schedule presets, truncated-run honesty | Sonnet | DONE (`f451936`). The orchestrator reviewed the screenshots over two fix rounds. Round 1 fixed: raw outcome strings, a cadence table that scrolled at 1024, and `ResultTrend` failing §3a (no time axis, no unit, a marker on every point, colour literals). Round 2 fixed durations in ms, a "Schedule:" label restating its heading, and a wrapping cron. The last polish put the change status in a full-width sub-row, so the permission name no longer breaks. The orchestrator re-ran frontend lint, typecheck, build and 796/796 tests. Open: `RunSearchButton.test.tsx` was seen to flake once under load (not touched). | §3a; screenshots at 3 viewports reviewed by the orchestrator | §3a; screenshots at 3 viewports reviewed by the orchestrator |
| T-039 | Docs: README configuration, upgrade note, deprecated settings | opencode `mimo-v2.6-flash-free` | DONE (`d2e37fc`). Verbatim insertion of the orchestrator's text, checked with `diff`. The model also flagged an ambiguous anchor in the spec and resolved it correctly. | §SC.9 |
| T-040 | **Live validation of search cadence** on the lab. Run Phase A, rule matches and WAF (if a WAF source is live) at 1 h and at 8 h. Check: one search per kind per tick; per-bucket rows equal a per-bucket control run; alerts still open on the generator's spike scenario. Also verify live that `GROUP BY <custom property>, starttime / bucket_ms` works for WAF, and whether wide searches time out (the Phase A split-on-failure question, §SC.6) ([spec](tasks/T-040.md)) | Sonnet | DONE ([report](tasks/T-040.report.md); §SC.10). 40/40 searches. One search per kind per tick held at 1 h and 8 h; Phase A 6/6 and WAF 3/3 buckets equal the per-bucket path; rule matches 5/6, the sixth off by 5 events in 2.4M from a QRadar `STOP` edge effect that favours the keyed path (follow-up T-048). No wide search failed; the lab is too idle to exercise truncation or failure. | Read-only; ≤ 40 Ariel searches |
| T-022c | Multi-role NGFW DSMs (FortiGate, Palo Alto PA Series, Check Point, Cisco FTD, Juniper SRX) lose their built-in FIREWALL type rule, so the site's naming decides ([spec](tasks/T-022c.md)) | opencode `mimo-v2.6-flash-free` | DONE (`8123988`). The orchestrator read the matrix restructuring: neutral-name rows keep their exact assertions, and the new rows assert no type rule claims a multi-role DSM. Re-ran 81 unit and 11 fleet integration tests. Design §C.3 updated. | Matrix updated only for the five; upgrade path test |
| T-031 | Write permissions `rule:write` and `coverage:write` for `PATCH /rules/{id}` and `POST /coverage/mappings`; allowlist entries removed ([spec](tasks/T-031.md)) | opencode `big-pickle` | DONE (`89b1ece`). Diff read; both mutations now audited too. Re-ran the guard test (70) and the Phase 3 API tests (67). | 403 for `read:*`; 403 before 404; audit actor; analyst role keeps working |
| T-041 | **D-8 research (live, read-only):** the QRadar custom-property catalog endpoints, the property→DSM-type join, quoted normalized fields ([spec](tasks/T-041.md)) | Sonnet | DONE ([report](tasks/T-041.report.md); checked for secrets by the orchestrator). 24/30 GETs, 3/3 searches. `regex_properties` + `property_expressions` joined on `identifier` give the per-type list; on the lab WAF type five properties apply and `WAF_URL` does not. | ≤ 30 GETs, ≤ 3 searches; no expression bodies |
| T-042 | **D-8 backend:** WAF profile admin API (§FM.4), async validation and migration 0015 (§FM.5) ([spec](tasks/T-042.md)) | Codex | DONE (`a365f15`). Round 1 (Codex resume): §FM.4 contract additions, lock-busy countdown + bounded retries, dev CORS `PUT`, merge conflict in the route-auth guard. Round 2: lock exhaustion forced `UNVALIDATED`, which would switch off a VALID source because its own collection held the lock; now it keeps the previous validation state and only clears the request. Orchestrator re-ran 2,133 unit and 664 integration tests; alembic 0014→0015→0014→0015 clean, no drift. | Every guard and exit path; the stale-version race; alembic round trip |
| T-043 | **D-8 backend:** property catalog provider method, cache and route (§FM.6) ([spec](tasks/T-043.md)) | Sonnet | DONE (`dadbe0d`). Diff read: `fields=` on both GETs, no body field in the DTO, failure uncached, MCP lacks the capability. Re-ran 87 unit and the API test. | No expression body anywhere; failure not cached; MCP unsupported |
| T-044 | **D-8 UI:** `/admin/waf-sources` and the editor, generic `fieldmapping` components (§FM.8) ([spec](tasks/T-044.md)) | Sonnet | DONE (`4938c9b`), after two screenshot reviews by the orchestrator (round 1 fixed: row separators under the first column only; raw `PROPERTY_REJECTED` text; unrealistic fixture; unlisted action value only addable as blocked; disabled primary button). Orchestrator re-ran lint, typecheck and 990/990. Backend side of the reconciled contract is owed by T-042. Earlier: review round 1 sent after the orchestrator read the screenshots (row separators under the first column only; raw `PROPERTY_REJECTED` text; unrealistic fixture; unlisted action value only addable as blocked; disabled primary button). Its 7 contract questions were reconciled in §FM.4 "Contract additions". | §3a; every state tested; screenshots at 3 viewports reviewed by the orchestrator |
| T-023 | **Phase C.1 (G1):** fleet summary and sources API, classification override and review, classification-rule CRUD, routing by source class ([spec](tasks/T-023.md)) | Sonnet | DONE (`8b27ca9`). Review round 1: expected EPS compared a 7-day mean with the window-end cell only; now the mean over every spanned weekday-hour cell, a source counting only when all are reliable; two `type: ignore` replaced by annotations. The orchestrator checked the weekday convention (`isoweekday`, as the engine), that inventory alerts already carry `source_class`, and re-ran 104 unit and 43+18 integration tests. A router conflict with T-043 resolved by keeping both. Open: WAF anomaly alerts carry no `source_class`. | Completeness never hidden; constant SQL at two fleet sizes; fingerprints unchanged |
| T-024 | **Phase C.1 UI (G1):** `/fleet`, `/fleet/sources`, `/admin/classification-rules`, plus the T-021 shell leftovers ([spec](tasks/T-024.md)) | Sonnet | DONE (`400f9d1`). Resumed after the session-limit cut; merge with T-044 resolved. Screenshot review round 1: the expected-EPS footnote misstated the API (it averages every spanned weekday-hour cell), overview source names broke at hyphens, a reason repeated in two cells, a heading over only a disclosure. Round 2: the overview table scrolled inside the page at 1024 with the action column hidden; fixed (severity under the issue at ≤1200 px), `color-scheme: dark` added, the capture script now flags any overflowing element. Orchestrator re-ran lint, typecheck, 1,066/1,066, build. Open: `backend/app/schemas/fleet.py` comment on `expected_eps` is stale (→ T-045). | §3a; every state tested; screenshots at 3 viewports reviewed by the orchestrator |
| T-046 | **Phase E.1b (G3):** rule statistics API, match rate, capacity history (migration `0016`, chained to `0014` until re-chained after T-042's `0015`) ([spec](tasks/T-046.md)) | Sonnet | REVIEW: diff read and accepted; **merge waits for T-042** so `0016` can be re-chained after `0015`. | Silence only where provable; benchmark labelled as such; constant SQL |
| T-030 | Live: lab IPS sources (PAN THREAT, FTD 430001) through auto-discovery, and what they parse, for Phase D and §FM.9 ([spec](tasks/T-030.md)) | Sonnet (the T-040 agent) | DONE ([report](tasks/T-030.report.md); checked for secrets; generators confirmed stopped). Both auto-discovered within seconds: PAN id 412 (type 206, raw CSV parsed — the T-029 risk did not materialise) and FTD id 413 (type 448). IPs, port and protocol 100 % normalized; signature via QID; **no action anywhere** — it needs an operator-authored custom property (§FM.9). 5/15 searches, 8/60 GETs. | ≤ 5 EPS; ≤ 15 searches; never API-created |
| T-048 | Follow-up from T-040: pad the Ariel `STOP` (not the `WHERE` bound) by a setting, capped at now ([spec](tasks/T-048.md)) | opencode `nemotron-3-ultra-free` | REVIEW: round 1 sent — the cap `min(end+pad, now)` could shrink `STOP` below the window end; must be `max(end, …)`; the reported unit count (407) was a subset. | Keyed rows equal per-bucket rows in the edge case |
| T-049–T-055 | **Phase D (G2, IPS):** designed in [`PHASE-D-IPS-BEHAVIOR.md`](PHASE-D-IPS-BEHAVIOR.md) §D.13 — profile and `/ips` admin API (D-8 for IPS), fleet-wide keyed collection, detectors, alerting, `/admin/ips-sources`, live validation, docs | see §D.13 | TODO | per task |
| T-047 | Scheduled searches: refuse a threshold that can never fire; truncation lower bound `value + 1` ([spec](tasks/T-047.md)) | opencode `big-pickle` | DONE (`dafe5ae`). Diff read; all providers cap `total_count` at `max_rows`; update checks the effective post-patch values. Re-ran 2,135 unit and 88 integration tests. | Every guard and exit path |
| T-056 | WAF anomaly alerts carry `source_class` ([spec](tasks/T-056.md)) | opencode `mimo-v2.6-flash-free` | DONE (`e5b75f7`). Instance name and class in one outer-joined statement, only on runs with opens: +1 statement per such run (the old `session.get` was an identity-map hit), still inside the scale pin (`open_cost` 8 ≤ 8). Re-ran 224 WAF/alert integration tests. | Routing matches; fingerprint unchanged |
| T-057 | **WAF analyst API (G2):** unified anomaly list/detail with `subject_kind`, WAF behavior read routes (§27.14), IPS-ready subject model ([spec](tasks/T-057.md)) | Sonnet | IN PROGRESS (`agent/t057`) | One `UNION ALL`; constant SQL; whitelist |
| T-045 | **D-8 docs:** README admin section; WAF design §27.2 rule 6 and §27.14 point to FIELD-MAPPING.md | opencode free model | TODO (after T-042 merges) | Verbatim text from the orchestrator |
| T-028 | **Security fix (DONE, `0e2bf71`):** the `/log-sources` (incl. PATCH and sync) and `/overview` routers were mounted with **no authentication**, and read routes of searches and alerts may be too. Router-level `requires()`, new permissions, an audit record on PATCH, and a guard test walking every route | Sonnet | IN PROGRESS (`agent/t028`) | Every route except the allowlisted health/docs returns 401 without a principal; read-only gets 403 on every mutation |

**Later, not yet scheduled:**

- WAF analyst API and UI (design §27.14–27.15).
- Field-availability pass against a real WAF product. Production onboarding is NO-GO until then.
- Phase A maintenance-history defect (design §28.14, item 4).
- Phase 4 hardening. **OIDC token verification is not implemented** (`_oidc_principal` fails
  closed with 501), so no authenticated route works in production yet. This blocks any production
  deployment, independently of the WAF NO-GO.
- Retire or rewrite `docs/NEXT-SESSION-PROMPT.md`, which still describes Phase 2.
- Document the upgrade procedure: stop `backend`, `celery-worker` and `celery-beat` before `docker compose run --rm migrate`. Every migration since 0002 adds NOT NULL columns whose backfill default is dropped, so a writer still running the old code fails until it is replaced.

---

## 4. Open decisions

| # | Question | Needed for |
|---|---|---|
| D-1 | ~~May the orchestrator commit and push?~~ **Resolved 2026-10-10:** yes, standing permission unless there is a hard block. | — |
| D-3 | ~~Should ratio paths also require source-level coverage?~~ **Resolved 2026-10-12: yes** (fail closed, AGENTS.md rule 8). When unparseable requests differ in kind, the visible proportion bounds nothing. Training and detection both require `endpoint_coverage_ratio >= waf_min_endpoint_coverage`. It may be relaxed only with live evidence. → T-017. | — |
| D-4 | ~~Should `WAF_NEW_ENDPOINT_ACCESS` get an enabling setting?~~ **Resolved 2026-10-12: no.** The B.2 enabling settings gate Ariel spend (extra breakdown searches). This detector adds none: it reads data already collected. It is OPERATIONAL whenever WAF collection runs. Revisit if operators need a per-detector mute once WAF alerting (T-016) is live. | — |
| D-5 | **Open (G3).** Where does a rule's logic (its tests) come from for the AI rule-analysis package? It is not in the 7.6 `/analytics/rules` payload. T-015 checks whether any GET endpoint returns it. | Phase E.3 |
| D-6 | ~~Lift the SELinux bind-mount ban?~~ **Resolved 2026-10-12 by the user:** private-label `:Z` is allowed only on a dedicated, secret-free directory (never the repo root, `.secrets/`, home or system paths). Shared `:z`, `label:disable`, permissive mode and relabel tools stay banned. AGENTS.md §5 and the orchestrator's memory updated. | — |
| D-7 | **Search cadence. Decided by the user on 2026-10-12; the design is pending T-032.** The user's note: let operators set how often the searches run, or QRadar drowns in searches. Options are 5 min, 15 min, 30 min, 1 h, 2 h, 4 h and 8 h; the default is 1 h. The user's answers: <br>• **Scope:** every search family, each set separately: Phase A volume, WAF collection, anomaly explanations, rule matches (`creEventList`) and operator scheduled searches. <br>• **Where:** set in the UI, stored in the DB, audited, with no restart. <br>• **Default 1 h everywhere, Phase A included.** The user accepted that an outage (`NO_EVENTS`) alert then arrives 1–2 h late. <br>**Orchestrator constraint:** the cadence must **not** change the bucket width. With 1 h buckets, Phase A gets at most 4 samples per weekday×hour cell in 28 days, against `baseline_min_samples=8`, so it would never train. The 100-event delta floor is also tuned for 5 min. The design is therefore one search per cadence tick returning every 5-minute bucket (time-keyed `GROUP BY`), each bucket judged as today. A further finding: the Phase A baseline builder groups by weekday×hour **without** filtering on `bucket_seconds`, so a width change on a live install would mix widths in one cell. | Search cadence build |
| D-8 | **Field mappings editable in the UI. Asked by the user on 2026-10-12:** custom-property names such as the WAF URI differ between environments, so they must be changeable from the UI; the same for IPS. **Design: [`FIELD-MAPPING.md`](FIELD-MAPPING.md).** <br>• The per-source WAF profile (mapping, versioning, validation, audit) already existed but was CLI-only. The design adds the admin API (§FM.4, the "separately reviewed admin API" of WAF design §27.2 rule 6; review in §FM.10), asynchronous validation with a never-stamp-a-stale-version rule (§FM.5, migration 0015), the QRadar property catalog filtered by the source's DSM type (§FM.6, T-041), environment prefill and copy-from (§FM.7), and the UI (§FM.8). <br>• **IPS:** Phase D is not built, so §FM.9 fixes the contract it inherits: normalized *or* custom field references, product presets for FTD and PAN THREAT, the same components. <br>• Orchestrator choices (the user may overrule): per-source profiles stay the unit (an environment is a set of sources); no bulk apply; a mapping change pauses collection until it validates (fail closed). | T-041–T-045; Phase D |
| D-2 | ~~Which opencode free models?~~ **Resolved 2026-10-10** by probing. Working: `opencode/mimo-v2.6-flash-free`, `ling-3.1-flash-free`, `longcat-2.5-preview-free`, `nemotron-3-ultra-free`. Unavailable or deprecated: `deepseek-v4-flash-free`, `kimi-k2.5-free`, `minimax-m3-free`, `glm-5-free`, `qwen3.6-plus-free`, `north-mini-code-free`, `exo-free`. | — |

---

## 5. Log

### 2026-10-12

- **Third session.** The user left a note at the top of this file asking for search cadence
  settings (5 min to 8 h, default 1 h). It is recorded as **D-7**, and the user's answers on scope,
  storage and default are in it.
  - The orchestrator found that cadence cannot equal the bucket width: a 1 h Phase A bucket never
    reaches `baseline_min_samples`. The cadence and the bucket width are therefore decoupled.
  - **T-032** (live research into a multi-bucket Ariel search) was launched on Sonnet.
  - T-029 was found cut off by the Codex usage limit.
- **Merges into `feat/phase-ce`:**
  - T-025, with the re-chain. The full gate was then green.
  - T-033 (`6c225f9`). After it, the full suite had **1 failure**: a T-025 test still pinned the
    old `Range` header (`items=0-199`). T-025's own truncation check (`LIMIT` and
    `len >= limit`) was correct throughout. Sonnet updated the pin to `items=0-200` as an
    approved single-line edit (`67b718b`).
  - T-029 (`a9d52d2`).
  - **Full suite on `a9d52d2`: 2,424 passed, 7 skipped, 0 failed.** ruff is clean, including
    the generator. mypy was 31, the baseline, after T-033; nothing in `app/` has changed since.
- **T-034 launched on Codex** at 18:45, after the user reported that Codex's usage limit had
  reset.
- **Search cadence completed (third session, evening).** The orchestrator reviewed every task
  before merging, as follows.
  - **T-034** (Codex): the core.
    - In review, `next_dispatch_at` was found off the new grid after a cadence change. The fix
      went back to the same Codex session.
    - `big-pickle` pinned the 0012 migration test to its revision.
  - **T-035** (Codex → Sonnet): Phase A.
    - Codex stopped correctly on `test_bounded_backfill`. The ruling: pin its cadence to 5 min,
      keep the assertion, and add a 1 h test.
    - Codex then hit its usage limit, and Sonnet finished the work. Sonnet fixed a lone-bucket
      rollback, swallowed evaluation errors, and a crashed run that was never recorded.
  - **T-037** (Sonnet): rule matches.
    - Five conflicting tests were ruled on. Three were kept by changing the formula to
      `ceil(cadence/window) + max - 1`; the beat pin went to 60 s; one `attempts` pin went to 1
      under split-first.
  - **T-038** (Sonnet): the UI. The screenshots were reviewed over three rounds.
    - Round 1: raw outcome strings, a 1024 overflow, and `ResultTrend` failing §3a.
    - Round 2: durations, a redundant label, and a wrapping cron.
    - Polish: the permission name broke mid-word.
  - **T-036** (Sonnet): WAF.
    - Four conflicting tests were ruled on.
    - The orchestrator found an unbounded WAF evaluation backlog at long cadences. The fix raises
      the bound to the cadence window when a run spans several buckets.
  - **T-039** (opencode `mimo`): docs, inserted verbatim.
- **opencode was re-authenticated by the user.** The orchestrator re-probed the free models with a
  tool-use test: 8 opencode and 9 OpenRouter `:free` models work (Resume here, environment
  notes).
- **Final full gate on `d2e37fc`: green.** Backend 2,685 passed; frontend 796/796; ruff clean;
  mypy at the baseline; alembic clean. `feat/phase-ce` was pushed at `2f13e6e`. Next: T-040.
- **The user set the product goals** (§0) and the working model: the orchestrator writes no code,
  and Codex and the opencode free models are used alongside Sonnet.
- **D-4: no setting.** T-006b reviewed and merged; its test edits were filters, not weakening.
- **T-009 merged; the branch went fully green.**
- **T-015 (live discovery).**
  - The token file in `.env` points at a container path, so it was overridden in the process
    environment only.
  - Findings: there are no type categories, and groups are empty in the lab. `average_eps` is
    unusable as volume, and the capacity fields are a static benchmark. No GET returns rule logic.
    `creEventList` grouping works.
  - Side effect: `/analytics/rules/{id}/dependents` started a QRadar task; it is now banned.
- **D-3: yes, fail closed.** T-017 merged.
- **T-016 (WAF alerting).** Merged. The full suite was 2,085 green.
- **T-019.** Codex's T-016 work hid a real defect: notifications crashed on any alert loaded from
  the database when a route matched. That hit Phase A recovery and the operator API. Fixed at the
  root, with the defect reproduced first.
- **T-018 and T-020.** Done by opencode free models and verified.
- **The user lifted the `:Z` ban** on dedicated secret-free mounts (D-6).
- **The user set the frontend quality bar** (AGENTS.md §3a); T-021 was launched.
- **T-010.** Docs merged, after one follow-up round.
- **B.2 merged into `main`** (`ed620a0`): 2,088 backend and 629 frontend tests, all gates green.
- **Phase C and Phase E designed** ([C](PHASE-C-LOG-SOURCE-FLEET.md),
  [E](PHASE-E-RULE-ANALYTICS.md)). T-022 (Codex) and T-025 (Sonnet) were launched in parallel.

### 2026-10-10

- **T-001, with a changed scope.** The plan was to split B.1, B.1.5 and B.2 into separate commits.
  That proved unsafe:
  - the three are interleaved in the same files (collector, models, providers, enums, WAF
    engine and baseline);
  - B.2 was started on top of B.1.5 the same evening;
  - no intermediate snapshot exists, in git or in opencode's snapshot store.

  What was done instead:
  - Took a backup ref of the full tree first: `refs/backup/pre-t001-2026-10-10` → `3e4dc45`.
  - Pushed `main` to origin. It held 9 unpushed commits, up to `6f17a73`.
  - Committed the whole snapshot as `7f8bd39` on a new branch, `feat/phase-b2`, and pushed it.
    `main` stays green and unchanged. The branch merges once B.2 is finished and every gate
    passes.
  - Secret scan of the diff before pushing: clean. The only UUIDs are test fixtures; the IPs are
    lab and test data.
- **T-002.** Wrote `AGENTS.md`. Found a trap while setting up worktrees: the virtualenv's editable
  install resolves `app` to the main checkout, so every command now carries `PYTHONPATH=.`.
- **T-003 started.**
  - Test DB `timescale/timescaledb:2.17.2-pg16` is up on `:5433` (tmpfs only, no bind mounts).
  - Created per-agent databases `qradar_obs_t004` … `qradar_obs_t008`.
  - The full suite is running on `qradar_obs_test`.
- **T-003 backend gates on `feat/phase-b2` (snapshot `7f8bd39`):**
  - **Full suite: 1,570 tests, 8 failed.**
    - 6 × `test_detector_capabilities::test_status_matrix`, and
      `test_phase_a_api::test_every_named_detector_is_published_with_its_status`. These two have
      one root: the B.2 types are published, but the tests do not expect them.
    - **`test_migrations::test_0009_…`.** A real defect: migration 0010 makes the
      `*_measurement` columns NOT NULL without a server default. Any 0009-era writer then breaks,
      including during a rolling deploy. → T-011.
  - **Finding:** the capability registry reports all five B.2 detectors `OPERATIONAL`, and
    `WAF_BASELINE_METRICS` claims the builder trains the four ratio metrics. In fact the engine
    runs none of the five and the builder trains none of the four. The registry over-claims.
    → T-005, T-006a/b, T-009.
  - mypy: 32 errors in 19 files, against the 31 / 18 baseline. The new one is
    `waf_endpoint_collector.py:572` → T-012.
  - `alembic upgrade head` + `alembic check` on a fresh DB: no drift.
  - Frontend: lint clean, typecheck clean, 629/629 tests, production build OK.
- **T-011 decision.** The Sonnet agent added `server_default`s to 0010, as first specified, and
  that broke `test_upgrade_from_phase2_preserves_data_and_adds_phase3`. That test pins a
  deliberate project convention:
  - backfill defaults are transient (migrations 0002 and 0004 document this);
  - the models use client-side defaults;
  - a fresh install and an upgraded database must be identical.

  0010 had followed that convention. The real defect was the 0009 test, which inserts 0009-shaped
  rows at `head`. The scope was revised to pin that test to revision `0009` and keep the
  convention. The rolling-deploy risk applies to every migration, so it is moved to "Later" as an
  operations item.
- **T-008 accepted** (`e4fcaf5`). 275 unit tests in `tests/unit/test_waf_behavior.py`; no
  detector defect found. Orchestrator verification:
  - re-ran the file: all pass;
  - ruff clean;
  - `backend/app` untouched;
  - three independent mutations — lower-bound branch removed, rise guard neutered, flood guard
    removed — each fail the suite.

  Agent notes for T-006a/b:
  - `detect_ratio` reads `ctx.baseline`, while `ctx.baselines` feeds only the volume context, so
    the engine must fill both;
  - concentration never runs under INCREASE_ONLY, by design.
- **T-011 accepted** (`8b709a4`). **T-007 accepted** (`c122b42`): 96 unit and 24 integration
  tests, re-run by the orchestrator on a fresh DB, all pass, content scanned. The agent's
  findings, all three reproduced by the orchestrator:
  1. `normalize_response_code("²")` raises ValueError → T-013.
  2. The unparseable-address placeholder poses as a client → T-013.
  3. **The scheduled pipeline never passes a breakdown plan**, so no breakdown, and hence no B.2
     detector, would ever run in production → T-014.
- **Round 1 merged** into `feat/phase-b2`:
  - **T-004** (`be79d8b`). The orchestrator replaced a backslash continuation inside the CLI
    docstring with a plain reflow.
  - **T-006a** (`b52a03d`), reviewed line by line. 101 WAF and engine integration tests re-run
    green. Noted: detector-state creation moved to one `INSERT … SELECT unnest … ON CONFLICT DO
    NOTHING RETURNING`, so statements stay constant with 5 detectors per endpoint; its
    lost-conflict path relied on an `assert`, fix folded into T-006b. Its one new unit failure
    (`test_waf_resilience::…declared_requirement`) only checks the legacy single `measurement`;
    B.2 specs declare per-input requirements. → T-009.
  - **T-005** (`53385f0`), reviewed. All WAF tests re-run green; the two red integration tests in
    its worktree were the known T-011/T-009 ones. Open design question raised → D-3.
  - **T-013** (`02374e9`): 253 tests re-run green.
- **Round 2 launched:** T-006b and T-014 (Codex, in parallel; disjoint files) and T-012 (opencode
  `ling-3.1-flash-free`). The full suite is running on the integration branch.
- Tooling note: pytest's project `addopts` already has `-q`, so adding another `-q` hides the
  summary line. Run without `-q`, or use `--junitxml`, to get counts.
- **Round 2 status:**
  - Full suite on the integration branch after round 1: **2,005 tests, 8 failed.** All 8 are the
    T-009 set; the migration test is green.
  - **Codex hit its ChatGPT usage limit** mid-task on both T-006b and T-014 (reset 19:24). It
    still exits 0 and writes no report, so always check the log tail. Both half-done worktrees
    were handed to Sonnet with the original spec and an instruction to review what they inherit.
  - **mypy is at 33 errors in 20 files.** Two are B.2 regressions:
    - `waf_endpoint_collector.py:572` → T-012 (diff correct, verification running);
    - `engine.py:443`, introduced by T-006a (an Optional comprehension type) → folded into T-006b.
  - **Rule breach by the free model on T-012:** it ran `mypy` in the main checkout instead of
    its worktree. The command was read-only and the orchestrator verified the main checkout is
    unchanged. From now on, free-model prompts put every command behind an explicit `cd` into
    the worktree.
- **T-014 accepted** (`8e00bbb`).
  - Sonnet found the inherited Codex code correct, and its tests passed on the first run.
  - The orchestrator read the test file and re-ran 127 WAF integration tests on a fresh DB: green.
  - Breakdown searches run inside the bucket's single Ariel permit, which is held longer per
    bucket.
  - Gap noted: no test covers a present-but-unvalidated action map. The `breakdown_plan` unit
    tests from T-007 cover the stale-map case.
- **T-012 accepted** (`75b2f1e`). The free model's diff is exactly the rename, and its report was
  accurate: it correctly identified the one remaining extra mypy error, `engine.py:443`, which is
  in T-006b's scope.
- **Delegation round 1:** T-004 (opencode free), T-005 and T-006a (Codex), T-007, T-008 and T-011
  (Sonnet, medium). All run in parallel, each in its own worktree with its own database.
- **D-2 resolved.** Free models probed; see §4.
- **Status assessment.** Last commit `6f17a73` (2026-08-03). B.1 and B.1.5 are complete per the
  design doc but not committed. An undocumented, half-built B.2 was found in the working tree; it
  is inventoried in §2.
- **Gates measured:**
  - Unit tests: 1,166 run, 6 failed (capability status table).
  - Ruff: 7 × E501.
  - Integration and frontend gates not run.
- **Agent tooling checked:**
  - Codex v0.162.0-alpha, inside the VS Code extension, logged in with ChatGPT.
  - opencode v2.0.24. GLM model IDs confirmed: `zai-coding-plan/glm-5.3` and
    `zai-coding-plan/glm-5.3-flash`.
  - Free-model IDs could not be listed headlessly: `opencode models` prints nothing without a TTY.
- **Orchestration model agreed with the user** (§1).
- Created this file.
