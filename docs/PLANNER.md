# Planner — orchestrator log

This file is the orchestrator's record of the project: what has been done, what is in flight,
who is doing it, and what comes next. The orchestrator updates it after every delegation and
every verification. Newest log entries go first.

If this file disagrees with the code, the code wins. Fix the file.

---

## ▶ Resume here (saved 2026-10-12, second orchestration session, in progress)

**Read §0 first.** The user set three product goals (G1 log-source fleet, G2 IPS/WAF behavior,
G3 rule analytics). Every task is scheduled against them.

**Working model:** the orchestrator writes **no code**, not even research scripts. Every coding
or probing job goes to an agent. The user asked (2026-10-12) that Codex and the opencode free
models be used alongside Sonnet.

**Branches:**
- `main` (`6f17a73`) is green and pushed.
- `feat/phase-b2` is pushed. At `543d7ff`: **2,055 tests passed, 0 failed**; ruff clean;
  mypy 31/18 (the baseline). T-006b and T-009 are merged.
- Backup of the original uncommitted tree: `refs/backup/pre-t001-2026-10-10` (local ref).

**In flight** (each in `../QRadar-Dash-wt/<id>`, with database `qradar_obs_<id>`):
- **T-015**, Sonnet, read-only lab discovery. Its report goes to `docs/tasks/T-015.report.md` in
  the main checkout. The lab token is at `.secrets/qradar.sec`, with the CA at
  `.secrets/qradar-ca.pem`. `.env` names the container path `/run/secrets/...`, so host-side runs
  override `QRADAR_API_TOKEN_FILE` and `QRADAR_CA_BUNDLE` in the process environment only.
- **T-016**, Codex, WAF alerting. Log: scratchpad `t016_codex.log`. Check the tail for "usage
  limit".
- **T-017**, Sonnet, D-3: the source-coverage guard on the ratio paths.
- **T-018**, opencode `mimo-v2.6-flash-free`, test-warning hygiene.

**Next steps, in order:**
1. Review T-016, T-017 and T-018 as they land, then re-run the full gates on `feat/phase-b2`.
2. `alembic check` on a fresh DB, plus the frontend gates (lint, typecheck, test, build).
3. **T-010**: design doc §30 and the README, covering B.2, D-3, D-4 and WAF alerting.
4. Merge `feat/phase-b2` → `main`.
5. From T-015's findings, write the specs for **Phase C.1** (G1) and **Phase E.1** (G3). They are
   independent and can run in parallel. Their migrations chain after 0010, so the orchestrator
   assigns revision ids.
6. Then follow the roadmap in §0.

**Environment notes:**
- The test DB container `qradar-observability-test-test-db-1` runs on `:5433`, on tmpfs, so its
  data is lost on restart. Restart it with `docker compose -f docker-compose.test.yml up -d
  test-db`, and recreate the per-task databases with `createdb`.
- The lab QRadar console is reachable from this host on 443.
- GLM credits return on 2026-10-14.
- Working free models: `opencode/mimo-v2.6-flash-free`, `ling-3.1-flash-free`,
  `longcat-2.5-preview-free`, `nemotron-3-ultra-free`.
- Task specs given to agents are kept in [`docs/tasks/`](tasks/).

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
| T-015 | G1/G3 research: live discovery on the lab QRadar. Log-source fields, groups and types; SIM Generic; REST `average_eps`; rule capacity fields; rule-statistics and rule-logic endpoints; a `creEventList` firing-count AQL | Sonnet | IN PROGRESS (stopped once on token path; resumed with process-env overrides) | `docs/tasks/T-015.report.md` answers the 11 questions with measured facts; read-only; ≤ 12 Ariel searches |
| T-016 | G2: raise alerts from WAF incidents (`waf_anomaly` fingerprint, `waf_alerting_enabled` shadow-mode switch, enqueuer wiring) | Codex | IN PROGRESS (`agent/t016`) | Open/refresh/resolve alerts; nothing in maintenance or with the setting off; statements constant with no transitions and on refresh |
| T-017 | B.2: decision D-3. Ratio and concentration training and detection require source-level coverage | Sonnet | IN PROGRESS (`agent/t017`) | New `low_source_coverage` tally; INSUFFICIENT_DATA below the floor; at-floor still judged; no assertion changed |
| T-018 | Test hygiene: drop the redundant module-level asyncio mark in `test_waf_collection.py` (5 PytestWarnings) | opencode `mimo-v2.6-flash-free` | DONE (re-run by the orchestrator: 23 passed, 0 warnings) | Same pass count, warnings gone |

**Later, not yet scheduled:**

- WAF analyst API and UI (design §27.14–27.15).
- Field-availability pass against a real WAF product. Production onboarding is NO-GO until then.
- Phase A maintenance-history defect (design §28.14, item 4).
- Phase 4 hardening.
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
| D-2 | ~~Which opencode free models?~~ **Resolved 2026-10-10** by probing. Working: `opencode/mimo-v2.6-flash-free`, `ling-3.1-flash-free`, `longcat-2.5-preview-free`, `nemotron-3-ultra-free`. Unavailable or deprecated: `deepseek-v4-flash-free`, `kimi-k2.5-free`, `minimax-m3-free`, `glm-5-free`, `qwen3.6-plus-free`, `north-mini-code-free`, `exo-free`. | — |

---

## 5. Log

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
