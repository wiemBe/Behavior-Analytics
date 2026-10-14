# Planner — orchestrator log

This file is the orchestrator's record of the project: what has been done, what is in flight,
who is doing it, and what comes next. The orchestrator updates it after every delegation and
every verification. Newest log entries go first.

If this file disagrees with the code, the code wins. Fix the file.

---

## ▶ Resume here (twelfth session, 2026-10-14, from ~21:15)

**The machine rebooted at 21:08 and `/tmp` was wiped.** Lost with it:
- every session scratchpad, with the agent logs, the UI-3.2 launcher and prompt, and the UI-3.5a
  round-2 PNGs (UI-3.5a was already merged);
- the screenshot fixture APIs;
- the T-054 scratch and partial report;
- the tmpfs test DBs. The test-db container was restarted.

The eleventh session had merged UI-3.5a (`6f88214`) and written the UI-3.3 spec before it ended.
**The user's rule from now on: nothing important under `/tmp`.** Logs, prompts, fixtures, PNGs and
agent scratch go in `/home/efe/Documents/QRadar-Dash-work/<task>/`, which is persistent and outside
the repository.

**Done:**
- **T-083 merged** (`fea4273`). The setup ERROR was proven to be a deadlock between the fixture's
  `DROP SCHEMA public CASCADE` and the TimescaleDB Telemetry Reporter. `clean_db` now terminates
  the other sessions on its own DB first: 4 errors in 55 runs before, 0 in 50 after. Re-verified
  here with 12 passed and ruff clean. The same fixture in 11 sibling files is T-083b.
- **UI-3.2 merged** (`b9295d1`) after two rounds. Round 2 replaced Arrange and Remove with one
  `Edit` disclosure; the measurement shows no added overflow and no taller title row for all 11
  widgets at 1440 and 1280. The orchestrator's gate: lint and typecheck clean; 144 files / 2,120
  tests; build OK. **The main checkout's `frontend/node_modules` lacks `react-grid-layout`**: run
  `npm install` there once T-090 (which symlinks it) is done.
- **T-087 merged** (`e60f06d`), after two rounds of screenshot review. Round 2 fixed:
  - the new-port detector: cumulative events and a materiality floor, with no robust score;
  - the one-sided z threshold;
  - an IPS percentage-point change multiplied by 100 twice;
  - clipping at 1024 in `/anomalies`.

  The orchestrator's gate: lint and typecheck clean; 152 files / 2,206 tests; build OK.
  **The same ×100 defect is on `main` for WAF** (`formatPoints` with `percentage_point_change`,
  which the backend already sends in points). **T-089 fixed it and is merged** (`73dbf8f`): the
  PNG reads "+29.7 pp"; the orchestrator re-ran 365 tests, plus lint, typecheck and ruff.
  The same review found the WAF new-endpoint view misstating its unit, its expected value and its
  threshold, and every WAF threshold drawn as "±". That is **T-090**; land it before `main`.
- **T-085 + B-2.3b merged** (`agent/t085`). The round-2 test fixes (big-pickle) now pin both
  guards: a mid-hour `until`, and an IPS-only RESOLVED check. Merged-tree gate: ruff clean;
  2,977 unit passed; 187 integration passed (histogram, IPS analyst/alerting/pipeline/scale,
  Phase A API/lifecycle, WAF analyst, migrations). Worktree, branch and DB removed.

**In flight** (work dir `/home/efe/Documents/QRadar-Dash-work/`):

| Task | Agent | Where | Collect |
|---|---|---|---|
| **T-083b** ([spec](tasks/T-083b.md)) | opencode `mimo-v2.6-flash-free`, `--standalone`, pid 18281 | `t083b`; DB `qradar_obs_t083b` | `t083b/agent.log`; `docs/tasks/T-083b.report.md` in `t083b` |
| **UI-3.3** ([spec](tasks/UI-3.3.md) + amendment) | Codex hit its usage limit at ~21:54 before writing a file (reset **02:17**). Now a **Sonnet sub-agent of this chat**, starting fresh | `ui33` (own `node_modules`); ports 3133/8133/9133 | `docs/tasks/UI-3.3.report.md` in `ui33`; PNGs `ui33/png/`; fixture `ui33/fixture/` |
| **T-091** lockfile in the image, patch upgrades ([spec](tasks/T-091.md)) | opencode `big-pickle`, `--standalone`, pid 163664 | `t091` (own `node_modules`) | `t091/agent.log`; `docs/tasks/T-091.report.md` in `t091` |
| **T-090** WAF new-endpoint units, materiality floor, one-sided threshold ([spec](tasks/T-090.md)) | Sonnet sub-agent of this chat | `t090`; ports 3190/8190/9190 | `docs/tasks/T-090.report.md` in `t090`; PNGs `t090/png/` |
| **T-054** restart ([spec](tasks/T-054.md) + amendment) | Sonnet sub-agent of this chat | `t054/`; DB `qradar_obs_t054` | `t054/T-054.report.md`; settles the fan-out coverage lead first. Check `pgrep -af qradar_lab_loggen` |

**Next:**
1. T-090 (WAF defects on `main`): merge before `main`.
2. UI-3.3 and T-091 (both running), then UI-3.5b.
3. T-083b; T-054, whose defects become fix tasks.
4. Full gate, then `feat/phase-ce` → `main`.

**T-088 done** ([report](tasks/T-088.report.md), big-pickle): none of the 22 `npm audit` findings is
reachable in this deployment.
- The two Next.js criticals need Windows hosting or `next/image`.
- The vitest criticals are dev-only.
- A patch round (`next` 15.5.28, `echarts-for-react` 3.0.6, `sharp`, `npm update` in range)
  clears the criticals.

**It also found a real gap:** `frontend/Dockerfile` ignores the committed lockfile
(`COPY package.json`, then `npm install`), so the image is not what the gates tested. **T-091**
([spec](tasks/T-091.md)) fixes both. Start it **after UI-3.2 merges** (both change `package.json`).
The vitest 4 major and echarts 6 are planned later.

**Agents:**
- **Codex is out until 02:17** (limit hit ~21:54, after UI-3.2's two rounds of ~375k tokens and the
  start of UI-3.3).
- GLM is out until **02:36**: "Usage limit reached for 5 hour" on the 21:20 probe.
- Sonnet: allowed by the user (~21:35).

---

## ▶ Resume here (eleventh session, 2026-10-14, from ~18:33; chat `qradar-dash-ca`)

The user: "planner.md oku devam et". The tenth-session chat (`qradar-dash-2c`) was still alive with
its T-054 Sonnet sub-agent; it **acknowledged a handover** (no commits, merges, launches or
collection from there; its T-054 agent may finish and write its report to its scratch).

**Done this session so far:**
- **T-085 reviewed and accepted** (three-family `_applicable_kinds`; IPS branch, still two
  statements; six GET-only `/behavior/ips/...` routes; whitelist detection; contributor lists
  deferred everywhere; the `test_waf_analyst_schemas.py` edit only extends the tuples). Committed
  on `agent/t085` (`48cf900`), then `feat/phase-ce` merged into it (`16f024f`; import conflict, both
  kept). **Not yet on `feat/phase-ce`**: the histogram is broken on `agent/t085` until B-2.3b.
- **UI-3.5a screenshots reviewed**: accepted with rulings (outage widget needs `read:fleet` +
  `admin:outage`; the "late by" rule; WAF labels from `validationView`); **round 2** for four small
  defects plus fresh PNGs ([spec §Round 2](tasks/UI-3.5a.md)).
- **T-087 written** ([spec](tasks/T-087.md)): after T-085, every `/anomalies` caller without a
  `subject_kind` receives IPS rows, and an IPS incident falls through to the Phase A page (IPS
  values drawn as EPS). T-087 gives IPS its own rows and investigation view. **Must land before
  `feat/phase-ce` → `main`.**

**In flight (this session's scratchpad:
`/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/27db6cd6-0ae7-4ff3-8508-d61ff41a6712/scratchpad/`):**

| Task | Agent | Where | Collect |
|---|---|---|---|
| **B-2.3b** histogram IPS branch ([spec](tasks/B-2.3b.md)) | opencode `nemotron-3-ultra-free`, `--standalone`, pid 1277672 | worktree `t085`; DB `qradar_obs_t085` | log `b23b.log`; report `docs/tasks/B-2.3b.report.md` in `t085`. Then gate `t085` and merge `agent/t085` → `feat/phase-ce` |
| **UI-3.5a round 2** | Sonnet sub-agent of this chat | worktree `ui35a`; ports 3135/8135/9135; scratch `ui35a-r2/` | "Round 2" section of `docs/tasks/UI-3.5a.report.md`; review the new PNGs yourself, then merge |
| **UI-3.2** drag/resize ([spec](tasks/UI-3.2.md)) | Codex, via the tenth session's `launch-ui32.sh` (started ~18:45 in this chat; it sleeps until **21:05**, then creates `ui32` from the then-current `feat/phase-ce`) | `ui32`; ports 3132/8132/9132 | log `…/a16a3a57-…/scratchpad/ui32-codex.log`; grep "hit your usage limit" |
| **T-083** | opencode `mimo-v2.6-flash-free`, pid 1212073 (from the tenth session) | `t083`; loops in `/tmp/t083/` | report `docs/tasks/T-083.report.md` in `t083`; check no loop process is left |
| **T-054** | Sonnet sub-agent of the **tenth-session chat** | `…/a16a3a57-…/scratchpad/t054/` | its report `T-054.report.md` there; its `driver.sh` waits for T-083 to exit before collection ticks |

**Next:** B-2.3b → merge `t085` (gate: unit + histogram/IPS/phase-A integration); UI-3.5a r2 →
merge; then T-087 (Sonnet) from the merged head; UI-3.3 and UI-3.5b after UI-3.2.

---

## ▶ Resume here (end of the tenth session, 2026-10-14 ~18:15; the user said "Stop", save)

**Start the next chat here.** The tenth session (chat `qradar-dash-2c`, from 16:20) stopped at the
user's request. No new work was started after "Stop"; the agents below were already running.

**State:**
- **`main` @ `55a5e37`** (pushed ~17:20): Phase D IPS detection and alerting, geo collection, the
  Phase 2 Home, top bar. Backend gate on `a4fdbf6`: 2,850 unit, 1,044 integration, alembic clean;
  frontend gate on `bdb8d1d`: 137 files / 1,981 tests, build compiled.
- **`feat/phase-ce` @ `c630c52`** (pushed). **Migration head `0023`.** Merged after `main`, each after
  the orchestrator's review and its own gate re-run: **B-4** (`4fcd21d`, saved dashboards: models,
  `0023`, API, RBAC, audit; 2,944 unit, 79 integration), **T-082** (`837691d`, production needs one
  origin; round 2 corrected the Docker peer-address claim), **T-086** (`7e01c78`, the redesign plan's
  UR.5/UR.7 match what is built), **B-2.3** (`696b630`, `GET /anomalies/histogram`; merged-tree
  2,962 unit), **UI-3.1** (`8168c10`, the widget registry, state union, catalog, the Home as an
  in-memory `DashboardView`; 2,034 tests). Before this session's end the earlier merges were T-084
  and UI-2.5 (both on `main`).
- **The user approved the Phase 2 screenshots** (~17:00). PNGs kept at
  `/home/efe/Documents/QRadar-Dash-screens/phase2/`. **Phase 3 is under way**, re-split by the
  orchestrator (recorded in UR.7 by T-086): UI-3.1 (merged), UI-3.2 drag/resize, UI-3.3 configure/
  expand/range, UI-3.5 the remaining catalog widgets (UI-3.5a now: the ones without configuration).
- **Rulings this session:** the seeded `analyst` role gets `dashboard:write` (`viewer` read-only);
  an admin gets 404 on another owner's PRIVATE dashboard; the 409 `detail` is
  `{message, current_revision}` (UI-4.1 must read it); the `--dg-*` grid numbers are the one accepted
  inline-style exception; the stale threshold with an unknown cadence is 4 h (UR.5.4); `active_only`
  is now honoured on `/anomalies` (UI-2.5).
- This session's scratchpad (logs, prompts, launcher):
  `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/a16a3a57-0bb1-488e-a4d5-dadbc9e16dfd/scratchpad/`.

**Open decision for the user — D-12, the project's name** (the user's note, 2026-10-14: rename
`qradar-dash` to "Secops Metrics Dashboard", or another name?). The orchestrator's view, given to the
user in chat: "Metrics Dashboard" undersells it (it detects and investigates behaviour, not only
metrics; "Dashboard" is one page of it) and the UI already brands itself "Behavior Analytics";
a rename touches the repository and folder name, the worktrees, the agents' memory path, the
compose project and the UI brand, so it is one deliberate task at a quiet moment, after the user
picks the name. **Not started.**

**In flight at save. Check each first.**

| Task | Agent | Where | How to collect |
|---|---|---|---|
| **T-085** IPS analyst read API ([spec](tasks/T-085.md)) | GLM (cut off) → Sonnet: **finished**, report written, **not yet reviewed or merged** | worktree `t085`; DB `qradar_obs_t085` | Its report: gates green (2,865 unit, 396 integration); it found and removed a "BREAK" mutation GLM left in `_applicable_kinds`; one edit to `tests/unit/test_waf_analyst_schemas.py` (outside its files; justified as forced by the new 3-tuple; review it). **Merging it breaks B-2.3's histogram:** `_applicable_kinds` now takes a required `ips_entity_id` and returns `(phase_a, waf, ips)`; adapt `histogram()` (pass `ips_entity_id=None`, unpack three, add the IPS branch: task **B-2.3b**) in the same merge round, then run the histogram tests |
| **UI-3.5a** catalog widgets without configuration ([spec](tasks/UI-3.5a.md)) | Sonnet sub-agent (cut off ~17:40, resumed ~18:12): **finished ~18:25, not yet reviewed or merged** | `ui35a`; PNGs `…/a16a3a57-…/scratchpad/ui35a/png/` | Its report `docs/tasks/UI-3.5a.report.md`: lint, typecheck clean; 143 files / 2,088 tests; build compiled; ten break-its; eight widgets `live`, none in the SOC Overview template; the catalog's "Already on this dashboard" is muted. **Review:** every PNG yourself (it notes WAF sources partly empty with 3 sources; names ellipsised at 1280 in Scheduled searches; holes until compaction); the one existing-test edit (the exact live-type list in `registry.test.ts`); **rule on** `table.outage_policy` requiring both `admin:outage` and `read:fleet` (it reads `/fleet/summary`); its "late by" rule (cadence + settle, 60 s tolerance) |
| **T-054** IPS live validation ([spec](tasks/T-054.md)) | ninth-session Sonnet (cut off ~17:48, no report) → **new Sonnet sub-agent of this chat** (~18:20) | old scratch (read only) `…/1f0011eb-…/scratchpad/t054/`; new scratch and report `…/a16a3a57-…/scratchpad/t054/T-054.report.md`; DB `qradar_obs_t054` | First hand: baselines trained, one scenario (`ips-port-sweep`) sent, 54 of 250 Ariel searches. **Candidate defect:** during the sweep `IPS_PORT_FANOUT_SPIKE` reads `INSUFFICIENT_DATA` ("destination-port coverage below the 90% floor"), so the fan-out detector may never fire on a sweep. The new hand settles that first, then runs the remaining scenarios; it waits for T-083 to finish before any collection tick. **If the chat closed, it probably stopped: check `pgrep -af qradar_lab_loggen` (stop by PID only) and its scratch, then hand over again** |
| **T-083** `test_migrations.py` setup-ERROR diagnosis ([spec](tasks/T-083.md)) | opencode `mimo-v2.6-flash-free`, `--standalone`, **opencode pid 1212073** (a duplicate run was stopped) | worktree `t083`; DBs `qradar_obs_t083`, `qradar_obs_t083b`; loops in `/tmp/t083/` | Report `docs/tasks/T-083.report.md`; the log `t083.log` is interleaved with a stopped duplicate (use the report). Check that no loop process is left (`pgrep -af t083`) |
| **UI-3.2** drag, resize, keyboard steppers ([spec](tasks/UI-3.2.md)) | **Not started.** A background timer was to launch Codex at 21:05; **the user stopped it** (~18:15, with "Stop") | spec and prompt ready: `ui32.prompt.md`; launcher `launch-ui32.sh` in the scratchpad (creates worktree `ui32` from the current `feat/phase-ce`, runs its own `npm ci`, starts `codex exec`; ports 3132/8132/9132) | Start it only when the user resumes: `bash launch-ui32.sh` (it waits until 21:05 if run earlier; edit the time or run its steps by hand). Grep its log for "hit your usage limit" |

**Next, in order:**
1. Collect T-085 → merge with B-2.3b (the histogram's IPS branch) → gate (unit, the anomaly and IPS
   integration files).
2. Collect UI-3.5a (screenshots yourself); start UI-3.2 (Codex) when the user resumes; then UI-3.3 (configure, expand,
   per-widget range) and UI-3.5b (the configurable widgets: `waf.endpoint_traffic`,
   `chart.eps_observed_expected` source mode).
3. Collect T-054; each defect it confirms becomes a fix task before IPS is called validated.
4. Collect T-083.
5. Phase 4 UI (UI-4.1 dashboard API client and switcher, UI-4.2 save/restore/templates, UI-4.3 `/`
   loads the user's default) after Phase 3; the backend (B-4) is already merged.
6. Merge `feat/phase-ce` → `main` after the next green full gate (backend unit + full integration,
   frontend gate).
7. The user's: D-12 (name); G-5 (first production read-only geo window); the ACTION custom property
   on the lab console (IPS block-ratio scenario).

**Agents at save:** Codex out until **21:04**. GLM out until **02:36**; **never two GLM sessions at
once** (two parallel sessions emptied the 5-hour window in ~33 min, twice today). Sonnet: the user
cleared it again (~18:10) after its session limit (~17:40). Free models: usable; they sometimes end a
run early with a plan instead of tool calls (tell them to work until the report exists). **Watch PIDs
carefully**: twice today a wrapper PID was mistaken for the agent's and a relaunch put two agents on
one worktree; take the PID from `pgrep -af <title>` (the line that starts with the opencode binary).

**Lessons this session:**
- Every premium agent family hit a limit today; the handover pattern (same spec + "review what you
  inherit") worked each time, and two second hands found real defects the first hand left (B-2.3's
  ignored `subject_kind`; T-085's leftover break-it mutation in `_applicable_kinds`).
- Look at every screenshot yourself: UI-2.5 round 1 passed its tests with ten visible defects.
- A plan's task split can be unbuildable (UI-3.1 and UI-3.4 could not be seen apart): re-split, record
  the ruling in the spec, and bring the plan document in line (T-086).

---

## ▶ Resume here (end of the ninth session, 2026-10-14 ~16:20; the user is moving to a new chat)

The ninth session's log follows this section.

**State:**
- **`main` @ `8814a47`** (unchanged this session).
- **`feat/phase-ce` @ `b4312b2`** (pushed). **Migration head `0022`.** Merged this session, each
  after the orchestrator's diff review and its own gate re-run: B-1.2b, G-2, G-2b, UI-1.5, B-2.1,
  UI-2.1, UI-2.2, UI-2.3, UI-2.4, T-051c, T-051d, T-052, T-055, T-055b, T-055c, T-080, plus the
  T-081 live report. **Phase D (IPS) is code-complete**; only the live validation T-054 remains.
  **Phase 2 (Home)** lacks only UI-2.5, the composition at `/`.
- **The full backend gate on `a4fdbf6`** (`gate` worktree; everything after it is docs only):
  ruff clean; mypy 31 errors in 18 files (baseline); **2,850 unit passed**; alembic `head`→`0014`→
  `head` clean, `alembic check` no drift, single head `0022`; **full integration on a fresh
  `qradar_obs_gate`: 1,044 passed** (9 min 41 s; log `scratchpad/gate-backend.log`). **The backend
  gate is green.** Only commits after `a4fdbf6` that touch code need a re-check (so far: none; the
  later commits are docs only).
- Scratchpad of this session:
  `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/`.

**In flight at save. Check each first.**

| Task | Agent | Where | How to collect |
|---|---|---|---|
| **UI-2.5** the Home at `/` ([spec](tasks/UI-2.5.md)): composition, one `WidgetFrame`, fix list (a)–(h) | **Codex** `exec` (pid 878464 at save), second hand after Sonnet hit its session limit | worktree `ui25`; capture ports 3125/8125 | Codex runs outside this chat and keeps going. Log `scratchpad/ui25-codex.log`, final message `scratchpad/ui25-codex.last.md`; grep the log for "hit your usage limit" (exit code lies). Then review the diff (no weakened test; the only allowed test edits are EPS strings changed by fix (f)), re-run the frontend gates, and **review the first screen at 1440×900, 1280×800, 1024×768 yourself** (the Phase 2 acceptance) |
| **T-082** docs: production needs one origin for the browser's API reads (CORS is `[]` in production; the top bar and Home read from the browser; uvicorn proxy headers and the rate limiter) ([spec](tasks/T-082.md)) | opencode `mimo-v2.6-flash-free`, `--standalone` (pid 895584 at save) | worktree `t082` | Log `scratchpad/t082.log`; report `docs/tasks/T-082.report.md`. Check its uvicorn/Next.js citations and that no config value changed |
| **T-054** IPS live validation ([spec](tasks/T-054.md)) | **Sonnet sub-agent of this chat** (from ~16:15): it **probably stopped with the chat** | main checkout, scratch `scratchpad/t054/`; DB `qradar_obs_t054` | Look in `scratchpad/t054/` for its scripts and partial report. **First confirm no generator process is left** (`ps aux | grep qradar_lab_loggen`; stop by PID only). Then hand to a new agent with the spec plus "review what you inherit" (Sonnet is cleared by the user for use; Codex needs `-c sandbox_workspace_write.network_access=true`) |

**Next, in order:**
1. Collect UI-2.5 and T-082 (above).
2. After UI-2.5 merges, run the **frontend gate** (lint, typecheck, test, build) on the head. The
   backend gate is already green on `a4fdbf6`; re-run the backend unit suite only if a commit after
   it touched backend code. If green, **merge `feat/phase-ce` → `main`** and push.
3. T-054 (above): each defect it finds becomes a fix task before IPS is called validated.
4. Free-model task ready to write: diagnose the intermittent `tests/integration/test_migrations.py`
   ERROR (seen twice under concurrent load; capture its text first). Run it only when no gate is
   using the DB server.
5. Small doc fix: README ~line 1016 says "UI, evidence and alert routing for WAF incidents are not
   built"; check against the code (the WAF analyst UI T-060 and WAF alerting exist) and fix.
6. Needs the user: **G-5** (the first production read-only geo window; until then a real country
   key is unproven live, see T-081); the **ACTION custom property** on the lab console (the IPS
   block-ratio scenario); Phase 3 (widget system, `react-grid-layout`) starts after `main`.
7. Later (UI-5.x): at 1024 the `INSUFFICIENT_DATA` pill wraps to two lines on `/behavior`;
   overlapping anomaly bands stack their alpha on `/behavior/sources/[id]`.

**Agent availability at save:** Codex in use (reset was 15:54). GLM out until **20:53** (its 5-hour
window was used up in ~40 minutes by two parallel sessions: give it one large task at a time).
Sonnet: the user cleared it for use ("bu oturumda sonneti kullanabilirsin"). opencode free models:
usable at any time (`mimo-v2.6-flash-free` and `nemotron-3-ultra-free` did well today; free models
take no UI work). The user also reminded: use the free coding agents too.

**Lessons this session:**
- Every agent family hit a limit today (Codex, GLM, Sonnet). The handover pattern ("review what you
  inherit" plus the original spec) worked each time: T-051c went Codex → GLM → Sonnet, and the third
  hand found the second hand's failing test was wrong, not the engine.
- Parallel UI tasks collided on a shared helper file (`lib/home/format.ts` add/add) and duplicated
  the widget frame. Name shared helpers' owners in parallel UI specs.
- A first KPI row passed its tests and failed the design bar (570 px cards). Always look at the
  first screen yourself.
- An agent ran `pkill -f next-server`: give every UI agent its own ports and the "PID only" rule.
- Free models fix the first occurrence of a stale sentence and miss the rest: ask them to grep.

---

## Ninth session log (2026-10-14, 13:20–16:20)

The user: "GLM can be used; Codex's session tokens ran out." Codex hit its usage limit in T-051c
round 2 (reset **15:54**; back and in use from 16:04).

**Agent limits today:** GLM out until **20:53**; Sonnet sub-agents hit a session limit at ~16:00
(cutting off T-052 round 2 and UI-2.5), and **the user then cleared Sonnet for this session**
(~16:15); Codex available from 15:54.

**State:**
- **`main` @ `8814a47`** (unchanged).
- **`feat/phase-ce`** (pushed at each merge). **Migration head `0022`.** Merged this session, after
  the orchestrator's diff review and its own re-run of the gates:
  - **B-1.2b** (`b4d5017`): ruff clean; 2,766 unit; 58 targeted integration.
  - **G-2** (`364a77e`): ruff clean; mypy 31; 2,844 unit; alembic `0021`↔`0022` clean, no drift,
    head `0022`; 66 geo and migration integration; frontend typecheck and 496 tests. The
    `catchall.test.ts` edit was accepted: it only makes room for `GEO_COLLECTION`. Two
    fail-open answers in `GET /geo/countries` went to **G-2b**.
  - **UI-1.5** (`558be59`), after round 2 (literals into tokens; the brand subtitle is visually
    hidden at ≤1200 px and stays in the accessibility tree). Screenshots reviewed at the three
    viewports; lint clean, 1,666/1,666 tests, build.
  - **G-2b** (`3d1fc55`): `measurement`/`remainders` null on `NOT_COLLECTED`; expected buckets
    over every enabled instance (the `_for_each_instance` predicate), still two statements.
    Ruff clean; 55 geo integration.
  - **B-2.1** (`94e3412`), after round 2 (**ruling:** the window end is floored to the step, so a
    7 d step is one whole UTC hour and one baseline cell; no weighting across cells). Ruff clean;
    mypy 31; 2,849 unit; 48 fleet integration; 3 statements at 3 and 40 sources.
  - **UI-2.1** (`6a110c4`), after round 2 (a failed refresh keeps the last good data with
    `refreshFailed`; `monitored_sources` required). Lint clean; 1,745 tests.
  - **T-055** (`0c44271`): README entries, IPS [BUILT] markers, D-11 in UR.2.2/UR.2.8 (GLM
    started, nemotron finished; defaults spot-checked against `config.py`). **Its README still
    says the IPS engine is not built: update it after T-051d.**
  - **T-051c** (`6f07f41`), third hand (Codex → GLM → Sonnet, the first two cut off by limits).
    Sonnet found GLM's failing new-port test was wrong (it never set the 1-day history), fixed
    per-source training that could teach one source from another's unjudged buckets, and fixed a
    pipeline crash. Gates re-run by the orchestrator: ruff clean, mypy 31, 2,764 unit, 86 IPS
    integration; on the merged tree 2,850 unit and 150 targeted integration (one run had a single
    unexplained ERROR that a re-run did not reproduce, likely the `test_migrations.py` hypertable
    flake G-2 reported: capture its text next time). The agent's full integration run: 930 passed.
    The import conflict in `tasks.py` was resolved by the orchestrator (both imports kept).
  - **UI-2.4** (`a3b7458`): top sources, recent anomalies, recent offenses, log-source health.
    Screenshots reviewed at the three viewports (professional; no overflow; long names
    ellipsised; null magnitude draws no bar); lint clean, 1,818 tests. **For UI-2.5 to fix:**
    (a) UI-2.2's `KpiFrame` and UI-2.4's `WidgetParts` duplicate the widget states: unify them into
    one `WidgetFrame`; (b) `recentOffensesRead` has no abort signal: add `homeReads.recentOffenses`;
    (c) at 1024 the "· Insufficient data" separator wraps to the start of a line in Top sources;
    (d) `tables.css` restates the scroll-edge cover with `32px`/`14px` literals: tokenize;
    (e) the Silent/Exceeding links lack an active-only filter on `/anomalies`; (f) UI-2.4's agent
    ran `pkill -f next-server` once (forbidden pattern; it may have stopped UI-2.2's server).
  - **T-051d** (`2e3bc1d`): `rebuild_due(..., log_source_id=...)`, one call per source (constant
    statements per call, pinned at 5 and 40 entities); each source trains through its own
    evaluated end; a failed or never-evaluated source is skipped alone; no cross-instance rebuild.
    Ruff clean, mypy 31, 2,850 unit, 74 IPS integration. The unscoped `rebuild_due` form is kept
    but has no caller.
  - **UI-2.2** (`2ff5ede`) after round 2: cards 137–221 px (one qualifier line, inline breakdowns,
    definitions in `title`/accessible description). Screenshots reviewed. The merge had an add/add
    conflict on `lib/home/format.ts` (UI-2.4's `formatEps`/`formatAgo` and UI-2.2's
    `signedPercent`), resolved by the orchestrator by keeping both; 1,878 tests green ×3.
  - **T-080** (`a7ce553`): the 500-property test pastes its query
    (`{ delay: null }`) with a 15 s per-test budget and a comment; every assertion unchanged.
  - **T-055b** (`e6dc89a` + `14e27a1`): README and Phase D markers say the engine is built; the
    orchestrator corrected one wording (per-source training is the design's intent, not a
    deviation).
  - **UI-2.3** (`6b2d54c`): fleet EPS (gaps shaded, never connected; one dashed "sum of
    per-source expected" line, no band), EPS by class (≤ 5 slices + an accessible list), severity
    per hour ("at least" when truncated). Screenshots reviewed; 1,958 tests. Two points went to
    UI-2.5: one EPS format; presets in the title row.
  - **T-081** ([report](tasks/T-081.report.md)): the G-2 geo keyed search **runs live** in both
    directions (accepted, ~1.1 s, 24 buckets, not truncated, the key-in-window check passed; 2 of 4
    searches). The lab's events carry only private and RFC 5737 addresses, so every key was `{}`
    (812,682 events `UNRESOLVED`): parsing a real country key, multi-country ordering and
    truncation stay unproven live until G-5 (the first production read-only window, the user's
    call). The lab Home map will honestly read `INSUFFICIENT_DATA`.
  - **T-052** (`a4fdbf6`): IPS alerting behind `ips_alerting_enabled` (default false); one bulk
    apply per bucket; fixed `ips_anomaly` fingerprint; maintenance respected; round 2 wired the
    notification enqueuer through `IpsInstancePipeline` and `dispatch_ips_collection`. The agent's
    full integration run (round 1): 1,042 passed. Round 2 was cut off by Sonnet's session limit
    after the code and tests and before the report; the orchestrator re-ran the gates (ruff, mypy
    31, 2,850 unit, 53 alerting/IPS/WAF integration) and wrote the round-2 report section.
  - The worktrees, branches and DBs of every merged task were removed.
  - Seen in review, for later (UI-5.x): at 1024 the `INSUFFICIENT_DATA` pill wraps to two lines in
    the `/behavior` "Needs attention" table.
- **Flaky test found:** `PropertyInput.test.tsx` "renders and filters 500 properties without a
  cap" failed once in about ten full runs under load (pre-existing, T-058) → **T-080**.
- **GLM hit its 5-hour limit at ~13:40** in both sessions (reset **20:53**). Codex is out until
  **15:54**. The user (~13:55): "Codex will take a while; give it to GLM or Sonnet."

**In flight (superseded by Resume here):**

| Task | Agent | Worktree, DB | Log / how to collect |
|---|---|---|---|
| **UI-2.5** the Home at `/` ([spec](tasks/UI-2.5.md)): composition, one `WidgetFrame`, fix list (a)–(h) | **Codex** (16:04), second hand: Sonnet was cut off by its session limit with partial work and no report ("review what you inherit") | `ui25`; capture ports 3125/8125 | Log `scratchpad/ui25-codex.log`, final message `scratchpad/ui25-codex.last.md`; grep the log for "hit your usage limit". The acceptance screenshots for all of Phase 2: review the first screen at each viewport yourself |
| **T-055c** docs: IPS alerting is built ([spec](tasks/T-055c.md)) | opencode `nemotron-3-ultra-free`, `--standalone` | `t055c` (no DB) | `scratchpad/t055d.log`; report `docs/tasks/T-055c.report.md` |
| **T-054** IPS live validation ([spec](tasks/T-054.md)) | Sonnet sub-agent (~16:15), live, read-only, ≤ 250 Ariel searches, generator ≤ 5 EPS | main checkout, scratch only; DB `qradar_obs_t054` (kept) | `scratchpad/t054/T-054.report.md`. Check the baseline strategy first; every defect becomes a fix task |
| **Full backend gate** on `feat/phase-ce` @ `a4fdbf6` | the orchestrator, `gate` worktree, fresh DBs `qradar_obs_gate`, `_gate_mig` | — | `scratchpad/gate-backend.log`: ruff, mypy, unit, alembic `head`→`0014`→`head`, check, heads, full integration |

**Next:**
1. Collect UI-2.5 (Codex), T-055c, the backend gate.
2. Then the **frontend gate** (lint, typecheck, test, build) on the head with UI-2.5, and if both
   gates are green, **merge `feat/phase-ce` → `main`**.
3. Collect **T-054** (Sonnet); each defect it finds becomes a fix task before IPS is called
   validated.
4. Still open, needing the user: **G-5** (the first production read-only geo window); the
   **ACTION custom property** on the lab console for the block-ratio scenario; Phase 3 (widget
   system) starts after `main`.

---

## Eighth session's end state (2026-10-14 ~12:00; superseded by the section above)

**State:**
- **`main` @ `8814a47`.** It was merged after a green full gate on `9597930`:
  - 2,598 unit + 895 integration passed;
  - alembic `0014`↔`0020` clean;
  - frontend 1,568/1,568 tests and build.
- **`feat/phase-ce` @ `18a4e9d`** (pushed). **Migration head `0021`.** Merged since the gated
  commit, so **not yet on `main`**:
  - **T-051a**: pure IPS detectors;
  - **T-051b**: IPS detection tables `0021`, the versioned baseline builder, and
    `ips_anomaly_fingerprint`;
  - **T-078** and **T-079**;
  - **UI-1.1**: charcoal tokens and self-hosted Inter, with D-11;
  - **UI-1.3 + UI-1.4**: the grouped text-only sidebar and restyled shared controls;
  - **B-1.2**: `instance_last_health_check_at` on `/overview`;
  - **B-1.3**: `GET /me` with `effective_permissions`;
  - **B-2.2**: constant-cost `/behavior/sources` and `/anomalies/summary`, plus
    `last_bucket_completeness`.
  - The orchestrator reviewed each one and re-ran its gates; the UI tasks after a screenshot
    review.
- **The user approved the UI plan** ([`UI-REDESIGN-PLAN.md`](UI-REDESIGN-PLAN.md)) with **D-11**
  (§4).

**In flight at save. Check each worktree first.**

Codex and opencode run as separate processes and keep going without this chat. The two **Sonnet**
agents were Agent-tool sub-agents of this chat and may have stopped with it. If one stopped, its
worktree holds partial work: hand it to a new agent with the original spec plus "review what you
inherit".

| Task | Agent | Worktree, DB | Log / how to collect |
|---|---|---|---|
| **T-051c** IPS engine, lifecycle, pipeline step, capability status ([spec](tasks/T-051c.md), **with Ruling 1**) | Codex, **resumed with Ruling 1** (session `01a1399f-80e3-77f3-ae89-ace62bfc533a`, pid 193245 at save) | `t051c`, DB `qradar_obs_t051c` | First run stopped correctly on a test conflict: `test_every_non_operational_detector_states_its_limitation` forbids a limitation on an operational detector. **Ruling 1:** the test stays; every scheduled IPS spec has `limitation=None`, and the block-ratio and signature conditions live in `activation`. Log `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/t051c-r1.log`, final message `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/t051c-r1.last.md`; grep for "hit your usage limit". Review: constant statements per (source, bucket) (`test_ips_scale.py`), evaluate-before-train, pinned capability edits as ruled |
| **G-2** geo collection backend, migration `0022` ([spec](tasks/G-2.md)) | Sonnet: **finished, awaiting the orchestrator's review** | `g2`, DBs `qradar_obs_g2`, `_g2_mig` | Report `docs/tasks/G-2.report.md`: ruff clean; mypy 31; **2,844 unit** passed; alembic round trip to `0021` clean, no drift, head `0022`; **full integration 967 passed** (re-run); frontend 1,606 tests; 17 break-it checks. **Before accepting:** (1) it edited `frontend/src/app/fleet/catchall/catchall.test.ts`, outside the allowed list, because that test pinned `CATCHALL_SENDERS` as the last family. Accept only if the diff merely makes room for `GEO_COLLECTION` at the end (the pre-ruled "pins the full list" case); (2) the first full run had one setup ERROR in `test_migrations.py::test_timescale_extension_and_hypertables` that the re-run did not reproduce. Watch it in the gate; (3) `measurement=EXACT` is returned with status `NOT_COLLECTED`, so consider `null` there (a small follow-up); (4) uniqueness uses a stored generated column `slot = COALESCE(country_code, remainder_kind)` in the primary key: check it against the CHECKs; (5) the search cap is shared, with SOURCE first; (6) the DTOs and `GeoDirection` live in `app/geo/fold.py` because `dto.py` and `enums.py` were out of scope; (7) the AQL uses the 3-argument `GEO::LOOKUP(...,'country','iso_code')` form proven by T-077, with no live run yet, so a live smoke is due before calling the map accurate (UR.6.4 G-5) |
| **UI-1.5** top bar (search, auto-refresh, bell, connection status) ([spec](tasks/UI-1.5.md)) | Sonnet: **finished, awaiting the orchestrator's review** | `ui15` (no DB) | Report `docs/tasks/UI-1.5.report.md`: lint, typecheck, **1,666/1,666** tests and build pass; `AppNav`/`shellFixes` unedited; 5 break-it checks; 84 PNGs listed in `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/ui15/pnglist.txt`, every capture without overflow. **Before accepting:** (1) review the PNGs yourself at 3 viewports; (2) **fix the §3a violation**: `globals.css` gained the dimension literals `64px`, `520px` and `44ch` outside `tokens.css`, so they move into tokens; (3) the brand and the top bar are a sticky `<header class="shell-head">` sibling above `.layout` (the agent's choice, so the bar does not scroll away): check the sidebar still paints full height; (4) at 1024 the search placeholder ends in an ellipsis; (5) a never-checked instance is forced neutral whatever its status (intended). **Open point, not this task's:** CORS allows only `localhost:3000` outside production and no origin in production, so browser reads need the app same-origin behind the proxy. Record it for deployment |
| **B-1.2b** stamp the instance health check and set `UNREACHABLE` on failure ([spec](tasks/B-1.2b.md)) | opencode `mimo-v2.6-flash-free`: **finished, awaiting the orchestrator's review** | `b12b`, DB `qradar_obs_b12b` | Changed `inventory_sync.py` and `tests/integration/test_log_source_collection.py`; report `docs/tasks/B-1.2b.report.md`; log `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/b12b.log`. **Before accepting:** re-run the gates yourself (free-model reports have misstated counts twice); check the new import `inventory_sync → waf_pipeline` (`sanitized_error`) creates no cycle; check that a failed sync persists `UNREACHABLE` plus the timestamp while the watermark does not advance |

**Next, in order:**
1. Collect the four above: review the diffs, re-run the gates yourself, then merge. Remove each
   merged worktree, branch and DB. Migration order: `0021` is merged; `0022` comes from G-2.
2. **GLM is allowed only after 13:00 (the user).** Give it the docs:
   - the README entries for C.2 (`outage_alert_default`, `catchall_*`), IPS (`ips_*`), geo
     (`geo_*`) and `GET /me`;
   - amend `UI-REDESIGN-PLAN.md` UR.2.2 and UR.2.8 to D-11 (`--text-3 #73757B`,
     `--border-strong #35363A`);
   - T-055, the IPS docs with the [BUILT] markers.
3. Write and launch:
   - **T-052**, IPS alerting behind `ips_alerting_enabled` (default false), after T-051c;
   - **G-3/G-4**: G-2 already includes the API; the `map.geography` widget comes in Phase 3;
   - **Phase 2 (Home)**, per UR.7: B-2.1 `GET /fleet/eps-series`, then UI-2.1 to UI-2.5.
4. **A full gate on `feat/phase-ce` → `main`**, once this round is in: ruff, mypy 31, unit +
   integration on a fresh DB, the alembic round trip, frontend lint, typecheck, test and build.
5. Seen in review, to fix later:
   - overlapping anomaly bands stack their alpha on `/behavior/sources/[id]` (UI-5.1);
   - in full-page captures the sidebar looks cut at 768 px high: a capture artefact, since the
     sidebar scrolls itself.

**Lessons this session:**
- The usage-limit watcher matched an old git log line ("cut off by its usage limit"). Watch
  for "hit your usage limit" instead.
- B-1.2's agent found that nothing writes `last_health_check_at` and that the instance status
  stays `HEALTHY` when QRadar is down → B-1.2b.
- Killing `opencode run` does not stop its session. The background `opencode serve` service
  keeps executing it: a "stopped" GLM run started a second pytest on a DB the orchestrator was
  testing. Stop a session with `opencode session export` then `opencode session delete <id>`.
  Launch background agents with `opencode run --standalone`.
- `big-pickle` failed once with "HTTP 400 {"model":"big-pickle"}" (provider-side); `mimo` took
  over T-078.
- nemotron's T-079 report claimed "287 passed" for the unit suite. The orchestrator's re-run
  gave 2,597. Always re-run the gates yourself.

---

## Eighth session, morning log (10:48–11:50)

The machine had rebooted: the test DB container (tmpfs) and the old
scratchpad were gone. The container was restarted and the DBs recreated. The scratchpad is now
`/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/0f3426b2-30b6-429b-8bf0-ec2479e06def/scratchpad/`.
GLM credits are back (`zai-coding-plan/glm-5.3` answered a probe). **The user: GLM only after
13:00.** The GLM run on T-076 Part B was stopped; the orchestrator finished its gates.
- **T-076 Part A was collected.** The diff was exactly the `down_revision` and the three tests. The
  orchestrator re-ran ruff and 22 migration and outage-policy integration tests, with a single head
  `0019`. Committed as `0e5bb8c`. `agent/t068` and then `agent/t070a` were merged into
  `feat/phase-ce` (`268f062`, pushed), and their worktrees and branches were removed.
- **Found:** `t051a` held the previous session's T-051a start: 1,386 lines, no tests, no report,
  cut off at 21:42 by an unknown agent. It was handed to Codex with "review what you inherit".
- The user, again: "ajanları kullanmayı unutma". Five threads in parallel:

| Task | Agent | Where | Log / output |
|---|---|---|---|
| **T-076 Part B** (merge, re-chain `0020` → `0019`) | opencode `glm-5.3` | `t069`, merge left uncommitted; DBs `qradar_obs_t069`, `_mig` | `scratchpad/t076b.log`; report appended to `docs/tasks/T-076.report.md` |
| **T-051a** IPS detectors, pure layer ([spec](tasks/T-051a.md)) | Codex | `t051a`, DB `qradar_obs_t051a` | `scratchpad/t051a.log`, `t051a.last.md` |
| **UI-R0** redesign plan ([spec](tasks/UI-R0.md)); docs only, launched early at the user's push for parallel agents | Sonnet | main checkout, writes only `docs/UI-REDESIGN-PLAN.md` | agent notification |
| **T-077** live, read-only `GEO::` AQL check (D-10) ([spec](tasks/T-077.md)) | Sonnet | scratch only | `scratchpad/t077/T-077.report.md` |
| **T-078** `/ips/sources` per-field status and reasons (T-053 open point 2) ([spec](tasks/T-078.md)) | opencode `big-pickle` | `t078`, DB `qradar_obs_t078` | `scratchpad/t078.log` |

Next, in order: collect T-076 Part B → merge `agent/t069`, then `agent/t070b` → full gate → `main`.
Then UI-R0 goes to the user for approval. T-053 open point 3 (`SOURCE_BUSY` as a status field)
was deliberately left out of T-078: it would need an audit query per list.

---

## Seventh session's state (saved 2026-10-13 ~22:40)

The UI redesign (UI-R0) was to wait until steps 1–3 of "Next steps" below were done.

**State at save:**
- **T-050b is DONE and merged** into `feat/phase-ce` (`64152e3`, migration `0018`), after four
  review rounds. Its worktree, branch and DBs were removed.
- **Finished and committed on their agent branches, all pushed to origin**, waiting for the
  migration chain:
  - **T-068**: `agent/t068`, `0019`; `feat/phase-ce` is already merged into it (`a24f055`);
  - **T-069**: `agent/t069` @ `0534a47`, `0020`;
  - **T-070a**: `agent/t070a` @ `25a7bb8`, which contains `agent/t068`;
  - **T-070b**: `agent/t070b` @ `7438958`, which contains `agent/t069`. It was accepted after one
    screenshot round: gap shading at every width, no dual axis, hostnames break only at `.` or `-`.
- **In flight: T-076 Part A** ([spec](tasks/T-076.md)). opencode `big-pickle` runs in the `t068`
  worktree:
  - log: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/718a4478-8643-430e-9cee-227f95f48d16/scratchpad/t076a.log`;
  - it re-chains `0019` → `0018`, and moves the drift checks of `test_migration_0018` and `_0019`
    to `head`;
  - at save, the alembic sequence was green (single head `0019`, no drift), and the full
    integration suite on `qradar_obs_t068` was running;
  - it runs in the background and continues without this chat.

**Branch cleanup (user request, ~22:00):**
- Every branch's content was verified to be in `feat/phase-ce` or `main` first.
- 25 worktrees, 58 local and 20 remote branches, and their test DBs were removed.
- **Remaining:** `main`, `feat/phase-ce`, and `agent/t068`, `t069`, `t070a`, `t070b` (local and
  origin); the worktrees `gate`, `t068`, `t069`, `t070a`, `t070b`; and
  `refs/backup/pre-t001-2026-10-10`.
- **Rule from now on:** once a task is merged, remove its worktree, its branch (local and remote)
  and its DB.

**Branch state:**
- **`main` @ `3f9c4fa`.**
- **`feat/phase-ce` @ `64152e3`** is pushed. It holds T-075 and T-050b. Its migration head is
  **`0018`**.
- Finished and committed **on their own agent branches**, waiting for the migration chain:
  - T-068 (`agent/t068`, `0019`);
  - T-069 (`agent/t069`, `0020`);
  - T-070a (`agent/t070a`).
- The main checkout is on `feat/phase-ce`.
- Before `main`: a full gate. `main` still carries the WAF time-bomb test failure and the 422
  "Add WAF source" picker; both are fixed on `feat/phase-ce`.

**User request, seventh session (2026-10-13, left as a note at the top of
[`UI_Suggestions/UI_Suggestion.md`](UI_Suggestions/UI_Suggestion.md); moved here at the user's
request).** Verbatim:

> BU NOT Orchestrator claude için. BU UI Önerileri codex ile birlikte oluşturulmuştur bu renk
> paleti ve bu dizaynı her yere uygulanmalıdır bu görevi elindeki görevlerden sonra
> başlayabilirsin home screende widgetler güzel mantık eklenirse tadından yenmez UI home screen
> ekran görüntüsü UI_Suggestions'ın altındadır bu dökümana refereans verebilirsin ve bu yazdığım
> notu planner.md ye kaydettikten sonra silebilirsin bu çoğunlukla UI polish herhangi bir işi
> overhaullama eğer öyle bir şey varsa bana sor dosyanın içerisindeki dosya yolları olmayabilir
> çok takılma

In English:
- Apply the palette and design in [`UI_Suggestion.md`](UI_Suggestions/UI_Suggestion.md) and the
  [home screenshot](UI_Suggestions/QRadar%20Observability%20Security%20Dashboard.png)
  **everywhere**.
- Start it **after the tasks in hand**.
- Home-screen widgets with real logic behind them are very welcome.
- It is **mostly UI polish**. **Do not overhaul any existing work.** Anything that would be an
  overhaul goes to the user first.
- File paths named in the document may not exist. Do not get stuck on them.

**Orchestrator reading (the plan → UI-R0, below):**
- **Polish, in scope.** The charcoal palette and semantic colours become the values in
  `tokens.css`, so no literals leave it. Also in scope: typography, the shell and sidebar, the
  Home layout after the screenshot, and every route restyled.
- **Overhaul or new capability. Ask the user before building:**
  1. A customizable widget grid: drag, resize, multiple dashboards and templates, with
     persistence. That needs a new table, an API and a new dependency (`react-grid-layout`).
  2. The world map. No geographic aggregate is collected today. It would need a new keyed Ariel
     search, which costs QRadar searches.
  3. "Events by category". There is no category data, and the screenshot's donut has six slices
     (§3a allows at most five).
  4. Global search, the Live toggle and notifications, where no backend exists.
- **§3a tension.** The screenshot's KPI row is four cards. They are acceptable only if each one
  carries real breakdowns and drills down, and is not a vanity number. Record the user's direction
  in AGENTS.md §3a when the work starts.
- The document's own execution order holds. Phase 0 is a plan in `docs/UI-REDESIGN-PLAN.md`,
  then the user approves, then one phase at a time, with screenshots reviewed by the
  orchestrator.
- **The user answered the four overhaul questions the same evening: decision D-10 (§4).**
  - The full widget system now.
  - A geo collection for the map, live-validated first.
  - EPS by source class in place of categories.
  - The top bar on existing APIs only.

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
  yourself. **The user, 2026-10-12 and again 2026-10-13: "… ajanlarını kullanmayı unutma"** — run
  several threads in parallel, always through agents.
- **Merging an old agent branch:** commit the agent's WIP on its branch, `git merge --no-ff
  --no-commit feat/phase-ce` into it, and let the agent resolve conflicts and re-gate on the
  integrated tree. Never `git add` the `backend/.venv` / `frontend/node_modules` symlinks.

**Branches:**
- **`main` @ `dabd744`** (2026-10-13 ~11:20): `feat/phase-ce` merged `--no-ff` after a green full
  gate on `921bf7e` (later commits planner-only): ruff clean; mypy 31 in 18 files; backend
  **2,177 unit + 763 integration passed**, 0 failed (fresh DB); alembic 0014→0016→0014→0016 clean,
  no drift, single head `0016`; frontend lint, typecheck, **1,095/1,095**, build.
- **`feat/phase-ce` @ `921bf7e`** (pushed; the main checkout is on it). Migration head **`0016`**.
  Merged this session, each reviewed and re-gated by the orchestrator:
  - **T-042** WAF profile admin API + async validation, migration `0015` (`a365f15`);
  - **T-046** rule statistics + capacity history, migration `0016`, re-chained after `0015`
    (`46ca49b`);
  - **T-024** fleet UI (`400f9d1`); **T-058** searchable property picker (`2eda938`);
  - **T-057** WAF analyst API (`fe5430a`);
  - **T-047** unreachable search thresholds; **T-048** Ariel `STOP` pad; **T-056** WAF alerts
    carry `source_class`; **T-059** deterministic double-submit tests; **T-045** D-8 docs.
- The full gate passed and `main` was merged (above). The `gate` worktree is detached again.

**In flight at save time (sixth session, ~16:10):**

- **Merged this session (`feat/phase-ce`, pushed; migration head `0017`):** T-061 (also `main` @
  `3f9c4fa`), T-064, **T-060** WAF analyst UI, **T-049** IPS profile + `/ips` (`0017`), **T-062**
  rule statistics UI, **T-053** `/admin/ips-sources` UI (`96a24ef`), **T-072** candidates routes
  accept a missing `instance_id` (live defect: "Add WAF source" got 422), **T-073** never-validated
  wording, **T-050a** IPS keyed searches + split driver + folds (`4061aae`), T-067 audit.
- **Incident (orchestrator error, fixed):** a worktree's `frontend/node_modules` symlink was
  committed by `git add frontend` and its merge replaced the main checkout's `node_modules` with a
  self-link; restored; `.gitignore` now ignores the symlinks (`6b3402b`). **Add explicit paths;
  check `git diff --cached --name-only`.**
- `main` still has the WAF time-bomb test and the 422 picker; both fixed on `feat/phase-ce`.

**Seventh session (from 20:12, 2026-10-13).** Collected the sixth session's agents:
- **T-074 is DONE** ([report](tasks/T-074.report.md)). Every keyed IPS AQL form was **accepted**
  live. In 6/6 searches, coverage was 1.0 and no result was truncated. Not exercised: skew,
  truncation, or fan-out above 2 ports.
- **T-068 round 1 was reviewed and accepted.** The class map is loaded once and only when a CLASS
  row exists. The orchestrator re-ran the unit suite (2,264 passed, 5 skipped) and the targeted
  integration tests (53 passed). It is committed on `agent/t068` @ `539b9c2`. It waits for `0018`
  before the re-chain and merge.
- Codex T-050b was resumed (`codex exec resume 01a1355d-…`). T-069 was relaunched on Sonnet.

| Task | Agent | Where | When it ends |
|---|---|---|---|
| **T-050b** IPS tables `0018`, entities, admission, collector, `IPS_COLLECTION` ([spec](tasks/T-050b.md)) | Codex **resumed** (session `01a1355d-31e8-74f1-be9f-e1e0016d460a`, log `…/718a4478…/scratchpad/t050b-resume.log`) | `t050b`, DBs `qradar_obs_t050b`, `_mig` | Check "usage limit"; review (constant SQL, watermark rule, LOWER_BOUND); gates; merge **first** |
| **T-050b round 3** (budget-exhaustion defect, below) | Codex, same session (log `…/718a4478…/scratchpad/t050b-r3.log`) | `t050b`; WIP committed `4951f3d` | Review; gates; merge **first** (`0018`) |
| **T-068** outage policy backend, `0019` → `0017` for now | DONE on `agent/t068` @ `539b9c2` | `t068` | Re-chain `0019` → `0018`; merge |
| **T-069** catch-all senders backend, `0020` → `0017` for now ([spec](tasks/T-069.md)) | DONE on `agent/t069` @ `0534a47` (Sonnet; 844 integration passed; the orchestrator re-ran 74 + 225) | `t069` | Re-chain `0020` → `0019`; merge |
| **T-070a** UI `/admin/outage-policy` ([spec](tasks/T-070a.md)) | DONE on `agent/t070a` @ `25a7bb8` after one screenshot round | `t070a` (contains `agent/t068`) | Merge after T-068's re-chain |
| **T-070b** UI `/fleet/catchall` + `CATCHALL_SENDERS` cadence label ([spec](tasks/T-070b.md)) | Sonnet | `t070b` (contains `agent/t069`), DB `qradar_obs_t070b`, ports 4771/3771 | Screenshot review; merge after T-069 |

**Rulings this session:**
- **`test_migration_0013`** (T-050b and T-069): setup-only change. The first upgrade goes to `0013`
  instead of `head`, and every assertion is kept.
- **`test_search_cadence_api`** (T-050b): the code changes, not the test. `IPS_COLLECTION` goes to
  the end of `SearchFamily`. At merge, `CATCHALL_SENDERS` (T-069) also goes at the end, after
  `IPS_COLLECTION`.
- **T-075:** the pinned key set gains the two new keys.

**Reviews that changed the code:**
- **T-050b round 3.** Budget exhaustion advanced the watermark past dimensions that were never
  searched, because the plan is dimension-major. Unsearched planned dimensions must now hold the
  watermark. Windows are atomic with respect to the budget. A window larger than the whole budget
  must show a visible condition.
- **T-070a round 1** fixed six points:
  - "Sources covered" mixed monitored and enabled counts;
  - the default grace and mode were unknown;
  - the instance badge showed a UUID;
  - the button words;
  - the source search was client-side;
  - the panel order.

**T-069 design note:** an unmeasured bucket has no row, which differs from §C.8's FAILED and
NOT_COLLECTED values. The watermark holds at the last measured bucket. A catch-all source with no
rows in a LOWER_BOUND bucket gets no row ("unknown").
| **T-075** `numerator_sum`/`denominator_sum` on `WafBaselineCellOut` ([spec](tasks/T-075.md)) | opencode `big-pickle` | **DONE, merged** (`c56a375`) | It stopped correctly on the pinned key-set test; ruling: add the two keys (the spec changes the contract on purpose). The orchestrator re-ran 83 integration tests. |

The T-060 follow-up "`bucket_seconds` on WAF list rows" was **dropped**: `WafAnomaly` has no
`bucket_seconds`, so it would need a join in the T-057 `UNION ALL`. The UI constant is correct
while WAF buckets are fixed at 300 s.

**Contract follow-ups still open:**
- T-060: "Robust score DEGENERATE". The WAF chart could now draw the pooled expected line from
  T-075's sums; that belongs in the redesign.
- T-053: `/ips/sources` per-field reasons, and a `SOURCE_BUSY` status field.
- T-070a: an instance-scoped class row has no covered count.

**Next steps, in order (updated ~22:40, end of the seventh session):**
1. **Finish the migration chain.** T-050b and `0018` are already merged.
   - **Collect T-076 Part A** from `t068`: read `docs/tasks/T-076.report.md` and the log tail, and
     review the diff, which should change only the down_revision and the three tests. Commit it on
     `agent/t068`. Then merge `agent/t068`, followed by `agent/t070a` (which contains `t068`),
     into `feat/phase-ce`.
   - **T-076 Part B, still to write and run.** In `t069`, merge `feat/phase-ce` in first. Then
     re-chain `0020` → `0019` and change the final drift check of `test_migration_0019` to `head`,
     as in Part A. Gates: a single head `0020`, and the full integration suite. Then merge
     `agent/t069`, followed by `agent/t070b`.
   - **Expect a conflict when T-069 merges:**
     - In `SearchFamily`, keep `…, RULE_MATCH, IPS_COLLECTION, CATCHALL_SENDERS`.
     - In `frontend/src/lib/api.ts` and `searchCadence.ts`, keep both entries, in the same order.
     - Both branches carry the same `test_migration_0013` edit.
     - Let the agent resolve it.
   - After each merge, remove the merged worktree, branch and DB.
2. ~~Review T-070b's screenshots~~: done and accepted.
3. **Full gate on `feat/phase-ce` → merge to `main`**: ruff; mypy at 31; the full unit and
   integration suites on a fresh DB; the alembic round trip; the frontend gates. This also fixes
   the WAF time bomb and the 422 picker on `main`.
4. **UI redesign (the user's request; D-10).** Start once the items above are collected.
   - Launch **UI-R0** ([spec](tasks/UI-R0.md); Sonnet or Codex). It writes only
     `docs/UI-REDESIGN-PLAN.md`.
   - The orchestrator reviews the plan, then **the user approves it before any code.**
   - Then one phase at a time, with screenshots: foundation (tokens, typography, shell) → home →
     widget system and persistence → every remaining page → QA. The geo collection runs in
     parallel: a live AQL `GEO::` validation first, then the collector.
5. **G1/G2 backlog**, in parallel with the redesign where agents allow:
   - T-071, C.2 live validation (a sender at ≤ 2 EPS shows up as NEW; a `RECORD_ONLY` class stays
     silent while the anomaly is recorded);
   - T-051 → T-052, the IPS detectors and alerting, then the IPS analyst view;
   - T-054, IPS live validation;
   - T-055, IPS docs;
   - README entries for C.2 (`outage_alert_default`, `catchall_*` settings).
6. **Later:**
   - E.3, the AI rule analysis (D-5, the user's call);
   - Phase 4 OIDC. **No authenticated route works in production until OIDC lands.**

**User decisions, sixth session (2026-10-13):** D-9 — outage alerts stay on for every monitored
source by default (`RECORD_ONLY` per class/source is opt-in); new SIM Generic senders go to a review
queue only, no alert.

**User decisions, fifth session (2026-10-13):**
- Asked whether the custom properties can be seen and mapped ("WAF'ta bu budur"): yes — T-043
  catalog + T-044 editor + T-042 API. **No automatic suggestion** ("öneri sunmayalım"); **a search
  inside the property list instead** ("aşağı kaydır veya ctrl f yapmıyım") → T-058, merged.

**⚠ Regression on `main` (found by T-060, fix first next session):** since T-057 the
`GET /anomalies` list returns Phase A **and WAF** rows when `subject_kind` is not given. The existing
pages `frontend/src/app/behavior/page.tsx` and `app/behavior/sources/[id]/page.tsx` call
`api.anomalies` without it, so on an install with WAF anomalies they render WAF rows with Phase A
components, in EPS. Fix: pass `subject_kind: "LOG_SOURCE"` there (and audit every other
`api.anomalies` caller), with tests; small, can ride with the T-060 merge or go alone first.
Invisible in the lab while no WAF anomaly exists, but wrong on `main`.

**Known open points (small):**
- **"No verdict" storage (T-033).** It is stored as `threshold_breached=False`. The alert evaluator
  tells it apart through `truncated`; a three-state column would need a migration.
- **WAF dispatch counts.** The dispatcher records its runs with `searches_sent = null` ("not
  counted"), because per-source tasks run asynchronously and are not aggregated.
- `test_waf_ariel_budget.py::TestPermits::test_permits_are_bounded_across_connections` flaked once
  under load (T-057 run); passed on re-run. Watch it; deflake like T-059 if it recurs.
- T-056: WAF alert open path now costs `open_cost = 8`, exactly the scale pin's ceiling; any further
  statement on that path breaks the pin (a deliberate signal, not a bug).
- T-057: `AnomalyRepository.list_anomalies` is unused since the unified list; left in place.
- T-058: the "current" item scrolls to the list's bottom edge rather than its middle (cosmetic).
- Closed this session: the threshold gap (T-047), the `STOP` edge effect (T-048), WAF alerts without
  `source_class` (T-056), dev CORS `PUT` (T-042), the `RunSearchButton`/`AlertActions` flakes (T-059).

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
    `01a131e3-49cc-7e32-8a85-a6975a548d1d`.
  - Fifth session: two T-042 review rounds by `codex exec resume` (no limit hit), then T-049 as a
    fresh `codex exec` at ~10:52.
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
- Free models that delivered clean work: `mimo` (T-022c, T-056, T-059), `big-pickle` (T-031, T-047,
  T-046b re-chain, T-045 docs), `nemotron-3-ultra-free` (T-048, after one review round: it first
  ran only a unit subset and had a cap bug).

---

## 0. Product goals (set by the user on 2026-10-12)

Every phase and task is scheduled against these three goals. When a piece of work serves none of
them, question it before starting it.

The "What exists" and "Gap" columns below are as of 2026-10-12 and are **not kept current** (for
example, WAF incidents now have alerting, an analyst API and a UI). The current state is in
"▶ Resume here" and the README roadmap.

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
| T-046 | **Phase E.1b (G3):** rule statistics API, match rate, capacity history (migration `0016`) ([spec](tasks/T-046.md)) | Sonnet; re-chain T-046b opencode `big-pickle` | DONE (`46ca49b`). `0016` re-chained after `0015`; the 0015 and 0016 round-trip tests pinned to their own revisions, drift still checked at head. Orchestrator re-ran 2,159 unit and 732 integration tests on the merged tree; single head `0016`. | Silence only where provable; benchmark labelled as such; constant SQL |
| T-030 | Live: lab IPS sources (PAN THREAT, FTD 430001) through auto-discovery, and what they parse, for Phase D and §FM.9 ([spec](tasks/T-030.md)) | Sonnet (the T-040 agent) | DONE ([report](tasks/T-030.report.md); checked for secrets; generators confirmed stopped). Both auto-discovered within seconds: PAN id 412 (type 206, raw CSV parsed — the T-029 risk did not materialise) and FTD id 413 (type 448). IPs, port and protocol 100 % normalized; signature via QID; **no action anywhere** — it needs an operator-authored custom property (§FM.9). 5/15 searches, 8/60 GETs. | ≤ 5 EPS; ≤ 15 searches; never API-created |
| T-048 | Pad the Ariel `STOP` (not the `WHERE` bound) ([spec](tasks/T-048.md)) | opencode `nemotron-3-ultra-free` | DONE (`agent/t048` merged). `STOP = max(end, min(end + ariel_stop_pad_seconds, now))`, default 60 s, on every query bounding `starttime` to `[start, end)`. Round 1 fixed a cap that could shrink `STOP` below the window end. Query versions not bumped (rule-match versions key continuity). After the merge no unpadded `STOP` remains; 2,164 unit tests green. | Keyed rows equal per-bucket rows in the edge case |
| T-049 | **Phase D (G2, IPS):** profile, read-only validation, `/ips` admin API, migration `0017` ([spec](tasks/T-049.md)) | Codex (died on its usage limit 11:27) → Sonnet | IN PROGRESS (`agent/t049`; Codex WIP `b09ea08`) | per spec |
| T-050–T-055 | **Phase D (G2, IPS):** designed in [`PHASE-D-IPS-BEHAVIOR.md`](PHASE-D-IPS-BEHAVIOR.md) §D.13 — profile and `/ips` admin API (D-8 for IPS), fleet-wide keyed collection, detectors, alerting, `/admin/ips-sources`, live validation, docs | see §D.13 | TODO | per task |
| T-047 | Scheduled searches: refuse a threshold that can never fire; truncation lower bound `value + 1` ([spec](tasks/T-047.md)) | opencode `big-pickle` | DONE (`dafe5ae`). Diff read; all providers cap `total_count` at `max_rows`; update checks the effective post-patch values. Re-ran 2,135 unit and 88 integration tests. | Every guard and exit path |
| T-056 | WAF anomaly alerts carry `source_class` ([spec](tasks/T-056.md)) | opencode `mimo-v2.6-flash-free` | DONE (`e5b75f7`). Instance name and class in one outer-joined statement, only on runs with opens: +1 statement per such run (the old `session.get` was an identity-map hit), still inside the scale pin (`open_cost` 8 ≤ 8). Re-ran 224 WAF/alert integration tests. | Routing matches; fingerprint unchanged |
| T-057 | **WAF analyst API (G2):** unified anomaly list/detail with `subject_kind`, WAF behavior read routes (§27.14), IPS-ready subject model ([spec](tasks/T-057.md)) | Sonnet | DONE (`fe5430a`). One `UNION ALL` (2 statements per page at any size); filters that cannot apply to a kind exclude it; whitelisted WAF detection block. Orchestrator re-ran 2,148 unit and 707 integration tests. Open: `test_waf_ariel_budget.py::test_permits_are_bounded_across_connections` flaked once under load. | One `UNION ALL`; constant SQL; whitelist |
| T-058 | **Searchable property picker** (user request 2026-10-13: no auto-suggestion, a search instead) ([spec](tasks/T-058.md)) | Sonnet | DONE (`2eda938`). Whole list, grouped, token search (`resp code` → `WAF_ResponseCode`), current value marked, Escape restores, live count; screenshots reviewed. | §3a; every state tested |
| T-059 | Deterministic double-submit tests (`AlertActions`, `RunSearchButton`) — orchestrator ruling to edit two tests | opencode `mimo` | DONE. A pending promise released by the test replaces the 50 ms timer; assertions kept. Frontend 1,095/1,095 three times under load. | Five green runs; mutation proof |
| T-060 | **WAF analyst UI (G2)** (§27.15) ([spec](tasks/T-060.md)) | Sonnet | IN PROGRESS. First pass done (`83d82c4`); screenshots reviewed by the orchestrator → [review round 1](tasks/T-060.review1.md) (14 points, rulings R1/R2) sent to a new Sonnet agent | §3a; screenshots reviewed by the orchestrator |
| T-061 | Regression from T-057: `/behavior` and `/behavior/sources/[id]` must pass `subject_kind: "LOG_SOURCE"` ([spec](tasks/T-061.md)) | opencode `mimo-v2.6-flash-free` | IN PROGRESS (`agent/t061`) | One test per page; break-it; frontend gates |
| T-062 | **Rule statistics UI (G3, E.1):** `/rules/statistics`, capacity history on `/rules/[id]` ([spec](tasks/T-062.md)) | Sonnet | IN PROGRESS (`agent/t062`) | §3a; every state tested; screenshots reviewed by the orchestrator |
| T-045 | **D-8 docs:** README admin section; WAF design §27.2 rule 6 and §27.14 point to FIELD-MAPPING.md; stale `expected_eps` comment ([spec](tasks/T-045.md)) | opencode `big-pickle` | DONE (verbatim, diff checked) | Verbatim text from the orchestrator |
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
| D-9 | ~~Outage alert default and new catch-all senders~~ **Resolved 2026-10-13 by the user:** outage (`NO_EVENTS`) alerts stay on for **every monitored source** by default (`ALERT`, today's behaviour); operators switch classes or sources to `RECORD_ONLY` (design §C.7). A new SIM Generic sender goes to a **review queue only**, no alert (`catchall_new_sender_alerts_enabled = false`, §C.8). | T-068, T-069 |
| D-10 | ~~UI redesign: which overhaul items?~~ **Resolved 2026-10-13 (seventh session) by the user, asked by the orchestrator.** <br>• **Widget dashboard: the full system now.** It covers a widget catalog, drag and drop, resize, and multiple dashboards persisted per user in the DB: a new table, migration and API, plus `react-grid-layout`, the dependency the user explicitly allowed. <br>• **World map: add a new geo collection.** First validate the AQL `GEO::` functions on live QRadar. Then collect a country-level aggregate under the search cadence. No raw IPs are kept (AGENTS.md rule 3). <br>• **"Events by category" becomes "EPS by source class":** the top 4 classes plus "Other", from existing data. <br>• **Top bar: existing APIs only.** Search runs over existing lists (sources, anomalies, rules). "Live" becomes auto-refresh with the last-updated time. The bell shows the open-alert count and links to `/alerts`. | UI redesign (UI-R0 onward) |
| D-11 | ~~UI plan questions (UR.10)~~ **Resolved 2026-10-14 by the user**, who also approved the plan ("Approved, start"). <br>• `/` becomes the widget Dashboard; `/behavior` stays as the behavioral triage page. <br>• Navigation is **text only**, with no icons. <br>• **Dark only**; no light mode. <br>• **Keep the palette's original values**: muted text `--text-3 = #73757B` and the input edge `--border-strong = #35363A`. The plan's contrast deviations (UR.2.2) are **not** applied, even though `#73757B` is below WCAG AA (3.70:1 on the panel). This is the user's explicit decision and overrides AGENTS.md §3a's §10 accessibility point for these two values only. <br>• **Orchestrator defaults** for the questions not asked: dashboards are built now, keyed on the principal's `subject`, and shared by `local-dev` until OIDC; `GET /me` is added (B-1.3); `/config-changes` is restyled, not removed. <br>• The plan's 17 collision rulings (UR.9) are accepted by the orchestrator, with C11 replaced by this decision. | UI-1.1 onward |
| D-2 | ~~Which opencode free models?~~ **Resolved 2026-10-10** by probing. Working: `opencode/mimo-v2.6-flash-free`, `ling-3.1-flash-free`, `longcat-2.5-preview-free`, `nemotron-3-ultra-free`. Unavailable or deprecated: `deepseek-v4-flash-free`, `kimi-k2.5-free`, `minimax-m3-free`, `glm-5-free`, `qwen3.6-plus-free`, `north-mini-code-free`, `exo-free`. | — |

---

## 5. Log

### 2026-10-13 (sixth session, 11:28–16:10)

- Codex died on T-049 (usage limit 11:27); Sonnet finished it. All four Sonnet agents hit the
  Claude session limit ~12:30 (reset 14:30) and were resumed with `SendMessage`.
- **Merged:** T-061, T-064, T-060, T-049, T-062, T-053, T-072, T-073, T-050a; T-061 also to `main`.
- **Live checks paid off:** T-065 found QRadar rejects `LIKE` on IP fields (→ `INCIDR`) before any
  IPS profile could validate; T-063 found the SIM Generic sender field (`logsourceidentifier`).
- **Defects found:** two wall-clock time bombs in the profile-admin tests (one on `main`); the
  "Add WAF source" picker 422 (missing `instance_id`, on `main`); "(last validated: None)".
- **Orchestrator error:** committed a worktree `node_modules` symlink; fixed (see Resume here).
- Designs: Phase C.2 (PHASE-C §C.7–§C.9); D-9 decided by the user.

### 2026-10-13

- **Fifth session (from 09:42).** The test DB container was restarted. Old agent branches (T-042,
  T-024, T-046) were brought onto the current integration branch by committing their WIP and
  merging `feat/phase-ce` into them, so each agent resolved conflicts and re-gated on the
  integrated tree.
- **Reviews that changed the code:**
  - **T-042**, two rounds. Round 1 added the §FM.4 contract shapes, the lock-busy countdown and
    CORS `PUT`. Round 2 found that lock exhaustion forced `UNVALIDATED`. That would switch off a
    VALID source just because its own collection held the lock, so exhaustion now keeps the
    previous state.
  - **T-024**, two screenshot rounds. The `/fleet` footnote misstated how expected EPS is
    computed, and the overview table overflowed at 1024 with its action column hidden.
    `color-scheme: dark` was added.
  - **T-048**: the `STOP` cap could shrink `STOP` below the window end.
- **Frontend flakes:** the two double-submit tests used a real 50 ms timer. T-059 made them
  deterministic. The suite then passed 1,095/1,095 three times under load.
- **User decision on the property picker:** no auto-suggestion; a search instead (T-058).
- **Merged:** T-047, T-024, T-056, T-042, T-046 (+T-046b), T-058, T-048, T-057, T-059, T-045.
- **Launched:** T-049 (Codex) and T-060 (Sonnet).
- **Full gate on `921bf7e` green; `main` @ `dabd744`.** The user ended the session there because
  of the context size.

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
