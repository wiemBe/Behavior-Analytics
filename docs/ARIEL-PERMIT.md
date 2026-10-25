# Ariel permits: one cross-process slot held for a search's whole lifecycle

Status: **built, 2026-10-22.** The permit is implemented by
`app/services/ariel_permit.py` and enforced around the provider lifecycle in
`app/providers/qradar_rest.py`; the build record remains in §P.9. This document generalises
the WAF-only budget of `app/services/waf_ariel_budget.py` `WafArielBudget` into a platform-wide
Ariel permit, and gives the two settings that are documented as concurrency caps
(`ARIEL_MAX_CONCURRENT_SEARCHES`, `ARIEL_GLOBAL_MAX_CONCURRENT_SEARCHES`) the enforcement they
have never had. It rests on [`SEARCH-CADENCE.md`](SEARCH-CADENCE.md) (§SC.5, the per-tick search
cap), on [`HISTORY-BACKFILL.md`](HISTORY-BACKFILL.md) (§B.6 the budget rule, §B.8 the cost model
and the measured search durations) and on [`PHASE-B-WAF-BEHAVIOR.md`](PHASE-B-WAF-BEHAVIOR.md)
(the WAF budget this design copies). Every claim about the code carries a `file` and symbol
citation checked on 2026-10-22. Numbers the code cannot give are labelled **estimates** with the
arithmetic shown, and the one number the repository does not state — what the appliance itself
tolerates — is marked **[C]** with the lab measurement that must answer it (§P.9, AP-8).

> **The user's decision (D-33):** generalize the advisory-lock budget into **one
> cross-process, crash-safe Ariel permit held for a search's whole lifecycle**, for collectors,
> scheduled and manual searches. Design first; the build follows this document.

## P.0 What is true today, and why the two caps are fiction

`ARIEL_MAX_CONCURRENT_SEARCHES` (3, `config.py` `ariel_max_concurrent_searches`) and
`ARIEL_GLOBAL_MAX_CONCURRENT_SEARCHES` (6, `config.py` `ariel_global_max_concurrent_searches`)
are documented as "Concurrent searches per QRadar instance" and "across instances"
(`docs/CONFIGURATION.md`, "Ariel guardrails"). What the code actually does:

| # | Fact | Where |
|---|---|---|
| F1 | Only `SearchExecutor` takes a limiter at all (`app/services/search_executor.py` `SearchExecutor.__init__`, parameter `limiter`) | `search_executor.py` |
| F2 | Both of its callers build a **fresh** `InMemoryConcurrencyLimiter` per call: `run_due_searches` per invocation (`app/workers/tasks.py` `run_due_searches`) and the manual route per request (`app/api/routes/searches.py` `run_search`). A limiter that is created per call bounds nothing but itself | `workers/tasks.py`, `searches.py` |
| F3 | `execution_instance_key` returns the literal `"default"` for every search (`search_executor.py` `execution_instance_key`), so the per-instance half of the cap has never distinguished instances | `search_executor.py` |
| F4 | No collector's search passes any limiter. The whole Ariel lifecycle (create → poll → results → cancel) runs inside the provider: `create_ariel_search` has exactly four internal callers (`qradar_rest.py` `QRadarRestProvider.get_log_source_metrics`, `get_log_source_metrics_bucketed`, `observe_property_rejection`, `_run_bounded_aggregate`) and one external one (`search_executor.py` `SearchExecutor._run_once`) | `qradar_rest.py` |
| F5 | `RedisConcurrencyLimiter` (`app/services/concurrency.py` `RedisConcurrencyLimiter`) has no caller; as written it would leak a crashed worker's slot forever under steady load (every `INCR` refreshes the shared key's TTL, so the key never expires) and it raises at once instead of waiting | `concurrency.py` |
| F6 | WAF already has the real thing: `WafArielBudget` (`app/services/waf_ariel_budget.py`), PostgreSQL session advisory locks on a dedicated `AUTOCOMMIT` connection, slot `i` = lock `(namespace, i)`, bounded polling wait, crash-safe because the lock dies with its connection, `WAF_ARIEL_MAX_CONCURRENT_SOURCES` = 2 | `waf_ariel_budget.py` |

The consequence (§P.1.2): the appliance's real concurrent-search count today is bounded by the
Celery worker's process count plus whatever the API process and CLI processes happen to be doing
— not by either setting.

## P.1 Inventory: every path that creates an Ariel search

### P.1.1 The paths, from the code

Every `create_ariel_search` call site in `backend/app` is one of the five in F4; each row below
reaches one of them. "In flight" is how many Ariel searches the path can have running at once:
collectors never run searches concurrently inside one task (no `asyncio.gather` or
`asyncio.create_task` anywhere under `app/collectors/`), so a path is either **one at a time**
(sequential inside its task) or a **fan-out** (one Celery task per unit).

| # | Family / feature | Code path | Reached from | In flight | Cadence / trigger | Bounds today |
|---|---|---|---|---|---|---|
| 1 | Phase A source volume (live) | `metric_collector.py` `MetricCollector._search_window` → `get_log_source_metrics_bucketed` (multi-bucket) and `get_log_source_metrics` (the single-bucket base case) | task `collect_metrics` (`workers/tasks.py`), beat every 60 s, cadence slot `PHASE_A_VOLUME` (5 min default) | one at a time per instance; instances sequential (`workers/tasks.py` `_for_each_instance`) | 5-min cadence, always on | `search_cadence_max_searches_per_tick` 16 incl. splits (`config.py`); 120 one-second polls per search (`qradar_rest.py`) |
| 2 | Phase A history backfill | the same `MetricCollector._search_window`, after the live pass (`app/history/families.py`) | the same task | one at a time | sub-budget `min(10, 16 − live)` (`history_backfill_searches_per_tick`; `app/history/families.py` `history_search_budget`) | as above, plus `history_backfill_seconds_per_tick` 120 |
| 3 | WAF live, per source | `waf_endpoint_collector.py` `WafEndpointCollector._fetch_window_under_permit` (one `WafArielBudget` permit per tick) → `get_waf_observations_bucketed` (2 searches: grouped + totals) and `get_waf_breakdown_bucketed` (1 per planned breakdown) | `dispatch_waf_collection` (beat 60 s, cadence `WAF_COLLECTION` 15 min) fans out one `collect_waf_source` task per eligible profile | **fan-out**, capped globally at 2 sources by `WafArielBudget` | 15-min cadence, off by default | permit wait 30 s (`waf_ariel_permit_wait_seconds`); 16-search tick cap; off-by-default breakdowns each add a search |
| 4 | WAF history (volume pass, then breakdown pass) | `WafEndpointCollector.history_volume_step`, `WafEndpointCollector.history_breakdown_step`, one WAF permit per step | the same per-source task | one at a time inside the step | sub-budget as row 2 | as rows 2–3 |
| 5 | WAF profile validation | `waf_profiles.py` `WafProfileService.validate` → `probe_waf_properties` (1 total + population + values per property; ≤ 3 properties → ≤ 7 searches) | route `POST /waf/log-sources/{id}/profile/validate` (202) enqueues task `validate_waf_profile`; CLI `python -m app.cli.waf validate` runs it inline | one at a time | on request (operator) | one-hour probe window bound (`WAF_PROBE_MAX_WINDOW_SECONDS`); no count cap |
| 6 | WAF rejection semantics probe | `app/cli/waf.py` `probe_rejection` → `observe_property_rejection` (1 search per property; the CLI passes 2) | CLI only (`waf probe-rejection`) | one at a time | on request | one-hour window bound |
| 7 | IPS live | `ips_collector.py` `IpsCollector._search` → `app/ips/keyed_search.py` `run_dimension` → `get_ips_keyed` | task `dispatch_ips_collection` (beat 60 s, cadence `IPS_COLLECTION` 15 min), one plan per instance | one at a time per instance | 15-min cadence, off by default | `IpsSearchBudget(search_cadence_max_searches_per_tick)` = 16 per tick per instance |
| 8 | IPS history backfill | `IpsCollector._run_history` (the same keyed searches) | the same task | one at a time | sub-budget as row 2 | as rows 2, 7 |
| 9 | IPS profile validation | `ips_profiles.py` `IpsProfileService.validate` → `probe_ips_fields` (≤ 10 searches: total, seven populations, two extras — its own docstring) | route `POST /ips/log-sources/{id}/profile/validate` (202) enqueues task `validate_ips_profile` | one at a time | on request | one-hour window bound (`IPS_PROBE_MAX_WINDOW_SECONDS`) |
| 10 | Catch-all senders | `app/catchall/keyed_search.py` → `get_catchall_senders_keyed` | task `collect_catchall_senders` (beat 60 s, cadence 5 min) | one at a time per instance | 5-min cadence, **on by default** | 16-search tick cap; row cap 2 000 |
| 11 | Catch-all history backfill | the same keyed search, history step | the same task | one at a time | sub-budget as row 2 | as rows 2, 10 |
| 12 | Rule matches | `rule_match_collector.py` → `get_rule_matches_windowed` (live and history) and `get_console_event_totals` (the history floor proof) | task `collect_rule_matches` (beat 60 s, cadence 5 min) | one at a time per instance | 5-min cadence, **off by default** | 16-search tick cap; row cap 2 000 |
| 13 | Anomaly explanations | `explanation_collector.py` `ExplanationCollector.collect` → `get_dimension_aggregates` twice (anomaly window, baseline window), each one `_aggregate_one_dimension` search per dimension | task `collect_anomaly_explanations` (beat 60 s, cadence 5 min) | one at a time per instance | 5-min cadence, **on by default** | `explanation_max_per_run` 5 anomalies per run × 2 windows × 10 dimensions = **≤ 100 searches per tick**; no per-tick count budget; 120-poll bound per search |
| 14 | Property watches (live and bootstrap history) | `app/property_watch/keyed_search.py` `run_search` → `get_pw_keyed` | task `dispatch_property_watch_collection` (beat 60 s, cadence 5 min) | one at a time per instance | 5-min cadence, off by default | `PwSearchBudget(pw_max_searches_per_tick = 40)`, 12 per watch |
| 15 | Geography | `geo_collector.py` → `get_geo_keyed`, one per direction (2 with the default directions) | task `collect_geo_countries` (beat 60 s, cadence 5 min) | one at a time per instance | 5-min cadence, **on by default** | 16-search tick cap |
| 16 | Scheduled searches | `search_executor.py` `SearchExecutor._run_once` → `create_ariel_search` → poll → results | task `run_due_searches` (beat 60 s) → `search_scheduler.py` `SearchScheduler.run_due`, sequential, ≤ `search_max_dispatch_per_cycle` 10 per cycle | one at a time within the task; one task per worker | each stored cron ≥ 5 min apart (`CRON_FLOOR_SECONDS`); ≤ 25 enabled | the fictional caps (F2); retry ≤ 3; timeout ≤ 900 s |
| 17 | Manual execution | the same executor | route `POST /searches/{search_id}/run` (`searches.py` `run_search`), in the **API process** | one per request; **no cross-request bound at all** | on request (`search:execute`) | a fresh private limiter per request bounds nothing |
| 18 | Property-watch preview | `app/services/property_watches.py` `run_preview` (`PREVIEW_SEARCHES` = 2) | route `POST /property-watches/{watch_id}/preview`, in the API process | one per request | on request, rate-limited `rate_limit_search_execution` 10/minute | the 2-search budget refuses a split; nothing bounds concurrent requests |
| 19 | CLI collection by hand | `app/cli/sync.py` (`log-source-metrics` → `collect_metrics`) | operator | one at a time | on request | as row 1 |

Rows 2, 4, 8, 11 and the history half of 12 and 14 are the history-backfill readers of
[`HISTORY-BACKFILL.md`](HISTORY-BACKFILL.md) §B.6.1: every family's history step runs inside the
family's own task, after the live pass, under the family's own lock. The permit design must cover
them without double-counting the live pass.

### P.1.2 The worst case in flight today, default install

Defaults (`config.py`): Phase A always on; `explanation_enabled`, `catchall_collection_enabled`
and `geo_collection_enabled` true; `waf_collection_enabled`, `ips_collection_enabled`,
`rule_match_collection_enabled`, `pw_collection_enabled` false. Worker: `celery ... worker
--concurrency=4 --max-tasks-per-child=200` (`docker-compose.yml`), i.e. **four prefork
processes**, each running one task at a time. Beat sends every family's dispatch task every 60 s;
the cadence gate (`search_cadence.py` `SearchCadenceService.claim_slot` over `slot_start`)
admits one tick per family per slot, and slots are epoch-aligned with a 120 s settle
(`search_cadence_settle_seconds`), so **every 5-minute family's slot opens on the same
wall-clock second**; every 15 minutes the WAF and IPS slots open on that same second too.

At that second, four Ariel-searching tasks are due at once and the four worker processes run
them concurrently. Each task holds at most **one** search in flight (sequential loops, §P.1.1).
So the appliance sees:

```text
worker processes            4   (each ≤ 1 search in flight)
+ manual runs (API process) N   (one per concurrent POST /searches/{id}/run; unbounded)
+ PW previews (API process) N'  (2 searches each, 10/minute rate limit)
+ CLI processes             M   (sync log-source-metrics, waf validate, waf probe-rejection)
= 4 + N + N' + M            with the documented global cap at 6
```

A default install with one manual search in flight and no CLI activity reaches exactly **5**
(one under the documented cap); a third concurrent manual run makes **7 > 6**, and nothing stops
it — F2. Raising the worker concurrency (a one-line compose edit) raises the first term linearly. With
WAF enabled the fan-out is the exception: `WafArielBudget` caps it at 2 sources world-wide, and
those two tasks occupy two of the four worker processes.

So today's honest statement is: **the only real cross-process Ariel budget on the platform is
the WAF one, and everything else is bounded by accident of process counts.**

## P.2 The mechanism: `ArielPermitBudget`

### P.2.1 One class, two slot pools

A new module `app/services/ariel_permit.py`, a direct generalisation of `WafArielBudget`
(this is deliberately the same construction; `WafArielBudget` stays untouched, §P.2.4):

```python
class ArielPermitBudget:
    def __init__(self, engine, settings, *, instance_id: uuid.UUID | None): ...
    @classmethod
    def for_session(cls, session, settings, *, instance_id): ...
    @property
    def enforced(self) -> bool: ...          # engine is PostgreSQL; else unbounded
    @asynccontextmanager
    async def permit(self) -> AsyncIterator[ArielPermit | None]: ...
    async def active_permits(self, session) -> ArielPermitCounts | None: ...
```

`ArielPermit` carries the slots taken (`global_slot`, `instance_slot`, either `None` when not
enforced) and `waited_seconds`, as `WafArielPermit` does. `permit()` yields `None` when the
bounded wait ends without a slot: **the caller must not issue its searches and must not treat
that as an empty result** — the same contract `WafArielBudget.permit` already documents.

**Slot pools.** PostgreSQL two-key session advisory locks (`pg_try_advisory_lock(ns, key)`):

| Pool | Lock | Count |
|---|---|---|
| global | `(ariel_permit_lock_namespace, g)` for `g` in `[0, G)` | `G = ariel_global_max_concurrent_searches` (6) |
| instance | `(ariel_permit_instance_lock_namespace, base + i)` for `i` in `[0, P)` | `P = ariel_max_concurrent_searches` (3) |

**Namespaces.** Today's two are `collection_advisory_lock_namespace` = 4711 (`config.py`,
collector locks) and `waf_ariel_permit_lock_namespace` = 4712 (WAF permits), and a validator
already refuses those two colliding (`config.py` `_waf_permit_namespace_is_its_own`). The design
adds `ariel_permit_lock_namespace` = **4713** and `ariel_permit_instance_lock_namespace` =
**4714**, and generalises the validator to require all four to be pairwise distinct. Different
first keys are disjoint lock spaces, so a permit slot can never collide with a collector lock or
a WAF permit.

**How an instance maps to its slot range, without a registry or a migration.** The instance pool
needs `P` contiguous slots per console, but instance ids are UUIDs. Derive the range from the id:

```text
ranges = 2**31 // P                      # P = 3 → 715 827 882 possible bases
h      = first 8 bytes of sha256(instance_id.bytes), big-endian
base   = (h % ranges) * P                # slots base .. base + P − 1, all ≤ 2**31 − 1
```

Two distinct consoles collide (share one range) with probability at most `k(k−1)/2 / ranges`:
at 10 consoles ≈ 6.3 × 10⁻⁸. A collision is a degradation, never a violation: the two
consoles share one `P`-slot pool (a tighter cap for both), and the global pool still bounds the
total. No small-integer column, no migration, nothing to allocate when a console is registered.

**Acquisition order: instance first, then global.** Both on the same dedicated connection, both
by `pg_try_advisory_lock` with a random start offset (the `WafArielBudget._acquire` pattern),
never by the queueing `pg_advisory_lock`. Order reasons:

- Holding a **global** slot while waiting for an instance slot (the order
  `InMemoryConcurrencyLimiter.slot` uses) wastes the scarcer-for-everyone resource: the task
  owns global capacity it cannot use while its own console is saturated.
- Holding an **instance** slot while waiting for a global one only consumes the caller's own
  console's share; its siblings were bounded by `P` anyway.
- Deadlock is impossible by construction regardless of order: try-locks never queue, one
  connection holds both locks, and every holder acquires in the same order. (The
  `InMemoryConcurrencyLimiter` comment about ordering concerns blocking semaphores; it does not
  apply to try-locks.)

On wait timeout the acquired half — if any — is released before `permit()` yields `None`.

**Bounded wait.** `ariel_permit_wait_seconds` (30) polled every `ariel_permit_poll_seconds`
(1), the `WafArielBudget._acquire` loop verbatim. The wait must stay comfortably below the
families' tick budgets (`history_backfill_seconds_per_tick` 120) so a yielding tick still has
time to report honestly.

### P.2.2 The dedicated connection, release, cancellation, crashes

Exactly the `WafArielBudget.permit` construction, kept because its guarantees are already argued
and tested (`tests/integration/test_waf_ariel_budget.py`):

- The permit lives on **one dedicated `AUTOCOMMIT` connection** taken from the process engine
  pool for the duration of the permit, doing nothing else. It is not part of any transaction, so
  a rollback or an aborted session in the run around it cannot prevent release.
- Release is an explicit `pg_advisory_unlock` of both slots in `finally` (global then instance,
  the reverse of acquisition; the order is cosmetic for session locks on one connection), then the
  connection returns to the pool.
- **Cancellation:** `asyncio` cancellation unwinds the `async with`, running the same `finally`.
- **Celery time limits / `SIGKILL` / OOM / lost host:** the backend ends the connection and
  PostgreSQL releases both session advisory locks. No TTL, no heartbeat, no leaked slot — the
  crash-safety argument of `WafArielBudget`'s module docstring, unchanged. A worker that is
  alive but stuck holds its slot until its own bound ends it: every lifecycle this design covers
  already has one (120 one-second polls in the provider, `timeout_seconds ≤ 900` in the
  executor).
- A process that dies between acquiring the instance slot and the global one leaks neither: both
  are on the same connection.

### P.2.3 SQLite (the unit suite) and the mock provider

As `WafArielBudget.enforced` and `CollectorAdvisoryLock._is_postgres` do today: on a
non-PostgreSQL bind the budget is **unbounded** — `permit()` yields a permit with `slot=None`
and zero wait, and the caller runs as before. The mock provider (`app/providers/mock.py`
`MockQRadarProvider`) never learns that permits exist: it keeps its own `create_ariel_search`
and the whole unit suite (mock-based collectors, determinism, statement-count pins) is
unchanged. Enforcement is a property of `QRadarRestProvider`, which is the only provider that
can run AQL at all (repository rule 2; `qradar_mcp.py` has no `AQL_EXECUTION` and blocks the
`create_ariel*` tool prefix).

### P.2.4 WAF keeps its own budget; the two can never deadlock

`WafArielBudget` is **not replaced** in this build. It bounds something wider than searches:
how many WAF *sources* may be mid-collection (fetch, fold, persist, evaluate, train) at once.
The Ariel permit bounds searches. A WAF tick will hold both, nested:

```text
WAF source permit (ns 4712)                    one per tick (WafEndpointCollector._fetch_window_under_permit)
  └─ Ariel permit per search (ns 4713/4714)    2 volume searches + 1 per planned breakdown, sequential
```

Deadlock is excluded by three independent facts: the namespaces are disjoint (§P.2.1); the
acquisition order is fixed everywhere (WAF permit outermost, then Ariel instance, then Ariel
global — no code path acquires in the other direction, because nothing that holds an Ariel
permit ever asks for a WAF permit); and every acquisition is a try-lock with a bounded wait, so
even a hypothetical cycle would end in a refusal, not a hang. Folding both budgets onto one
shared slot-scan helper is a later cleanup (§P.10 Q5), deliberately not in this build: WAF is
built, reviewed and lab-validated, and this design must not destabilise it.

### P.2.5 Priority: live collection, background work, reserved slots

**Live collection must not starve behind history or scheduled searches.** The mechanism is a
reservation inside the global pool, not a queue (queues would reintroduce the crash-safety
problem the advisory locks solve):

- Every permit request carries a **class**: `live` (the default) or `background`.
- A `background` request may only take global slots with index **≥ `ariel_permit_reserved_live_slots`**
  (`R`, default 2). A `live` request may take any slot.
- Consequence at the defaults (`G = 6`, `R = 2`): background work alone can never hold more than
  4 slots; slots 0 and 1 are reachable only by live collection, so **live collection always has
  at least 2 slots available**, whatever the history backfill or the scheduled-search backlog is
  doing. Live can still crowd background out entirely — that is the intended direction: history
  and scheduled searches defer, a collector tick never waits behind them.

No counting query is needed (the reservation is *which slots may be tried*, so it is the same
crash-safe try-lock), and it composes with the per-instance pool unchanged: the reservation is
global only, because the instance pool is already per-console.

**Who is background** — everything whose honest answer to "not now" is "later, on my own
schedule":

| Caller | Class | Why |
|---|---|---|
| every collector's **live** pass (rows 1, 3, 7, 10, 12, 13, 14, 15 of §P.1.1) | live | the platform's purpose; a deferred tick is a data gap |
| `app/cli/sync.py` collection by hand | live | it runs the very same live collectors |
| every **history** step (rows 2, 4, 8, 11, 12, 14) | background | §B.6's "live first" rule, now enforced by construction |
| scheduled searches (`SearchScheduler`) | background | an operator's stored query must not delay collection |
| manual execute (`run_search`) | background | a human may retry; the console's data may not wait |
| PW preview (`run_preview`) | background | a convenience view |
| profile validation and the rejection probe (rows 5, 6, 9) | background | operator-triggered, retryable |

**How the class reaches the permit.** The provider must stay ignorant of who is calling it (it
is also why the permit cannot be purely a factory-time object): a `with
ariel_background_priority():` context manager in `app/services/ariel_permit.py` sets a
`contextvars.ContextVar` for its scope and restores it on exit — task-local by construction,
never process-wide state. History drivers, `SearchExecutor._run_once`, `run_preview`, the two
profile-validation services and the WAF CLI wrap their provider calls in it. The permit reads
the contextvar at acquire time. (This is the context variable's **only** job: priority. The
permit *source* is injected, §P.3.)

## P.3 Where the permit is held

**One permit per lifecycle, so every search is covered once and none is counted twice.** The
code has exactly five lifecycle owners (F4): four provider methods that run create → poll →
results (→ cancel) internally, and the executor, which drives the same lifecycle through
`create_ariel_search`, `get_ariel_search_status`, `get_ariel_search_results` and
`_safe_cancel` itself. Compared placements:

| Placement | Coverage | Verdict |
|---|---|---|
| **A. Inside each lifecycle owner (chosen).** The four provider methods open `permit()` around their whole body (for `observe_property_rejection`, whose loop is one search per property, one permit for the whole call — the WAF "one permit per tick" precedent); `SearchExecutor._run_once` opens it around create → poll → results → cancel, replacing `async with self.limiter.slot(...)` | Every search, by construction: the five owners are the only `create_ariel_search` call sites in `backend/app`. No double count: the four methods never call each other, and the executor calls the raw `create_ariel_search`, not `_run_bounded_aggregate` | **chosen** |
| B. Around each caller (every collector, task, route, CLI) | Also complete if done everywhere, but it is ~19 call sites (§P.1.1), each needing its own fail-closed handling, and the **next** caller can simply forget — exactly how the current caps came to be fiction | rejected: fragile, and the guard test would have to enumerate callers forever |
| C. A wrapper provider (`PermittedProvider`) built by the factory | Cannot know lifecycle boundaries: the executor calls create and poll as separate methods, so a wrapper would have to hold a permit across calls with hidden state, and would also wrap the MCP provider's read-only surface with permit logic it can never use | rejected: hidden state, wrong shape |
| D. Acquire inside `create_ariel_search` alone, release on terminal state | Counts the *creation*, not the lifecycle: the poll loop and the results fetch — the expensive part on the appliance — would run unsynchronized, or the permit would have to be attached to the handle and freed by whichever method happens to observe a terminal state | rejected: under-covers or stateful |

**How the provider gets a permit source without a database session today.** Injection through
the factories, the same path every other policy setting already takes (`ariel_stop_pad_seconds`
reaches the provider exactly this way, `app/providers/factory.py` `build_provider_for_instance`):

- `QRadarRestProvider.__init__` gains `permit: ArielPermitBudget | None = None`. `None` (the
  default, and what every existing direct construction in tests passes) means **no enforcement**:
  the ~14 test constructions and the unit suite are untouched.
- `build_provider_for_instance` passes
  `ArielPermitBudget.for_session(session_or_engine, settings, instance_id=instance.id)` — the
  run's own engine, the console's slot range. (`_for_each_instance` already hands the factory a
  session for MCP auditing; the engine is `get_engine()` when no session is in scope.)
- `build_provider()` — the process-wide default used by `run_due_searches` and the API's
  `get_provider` dependency — passes a budget with `instance_id=None`, which skips the instance
  pool and takes global slots only. That matches the model: scheduled searches are not
  instance-scoped yet (`execution_instance_key` returns `"default"`); when Phase 3 wires real
  instance ids into searches, the executor starts passing the instance and the per-instance cap
  begins to bind with no further change.
- The mock and MCP branches of both factories pass nothing.

**What this placement leaves alone, on purpose:** the MCP provider (no `AQL_EXECUTION`, nothing
to permit — repository rule 2 keeps holding); the mock provider and the whole mock-based unit
suite; the collector statement-count pins (`tests/integration/test_behavior_bulk_reads.py`,
`test_catchall_collection.py`, `test_waf_scale.py` shape) — the permit's dedicated connection is
not the session those tests count statements on; and `app/anomaly/capabilities.py` (a permit
refusal is not a detector input; §P.4 keeps verdicts honest).

## P.4 When no permit comes

A typed error, not a bare `None` leaking: `ArielPermitUnavailable`, raised by the provider
methods and by `SearchExecutor._run_once` when `permit()` yielded `None`. It subclasses
`ProviderUnavailableError` so every existing `except ProviderError` path in the collectors —
the paths that already implement fail-closed (repository rule 8) for a failing appliance —
treats "no capacity" exactly like "no answer": **no bucket, no watermark advance, no zero**
(`MetricCollector._search_window` keeps only the prefix that completed; the WAF collector keeps
its watermark and records the failure class; catch-all/IPS/geo/PW/rule-match each take their
existing `FAILED` paths, which write nothing). The WAF tick's outer `WafArielBudget` refusal
keeps its own, more precise `capacity_unavailable` report (`waf_pipeline.py`), which this design
does not change.

Per caller, what the refusal means and says:

| Caller | Behaviour on refusal | What it says |
|---|---|---|
| collector live tick (rows 1, 7, 10, 12, 13, 14, 15) | the search fails; buckets before it are kept; the watermark does not advance; the tick's outcome is `failed` (`workers/tasks.py` `record_run`) | the watermark's `last_error` carries `ArielPermitUnavailable: no permit within 30 s` (the class-and-message pattern the collectors' error fields already use, `qradar_rest.py` `_aggregate_one_dimension`); the task logs one WARNING naming family, waited seconds and the caps |
| collector with its own report (WAF row 3) | unchanged `capacity_unavailable` when the *WAF* permit is refused; an inner Ariel refusal surfaces as a provider failure with the class name | the existing reason text (`waf_pipeline.py` `WafSourcePipeline.run`) |
| history step (rows 2, 4, 8, 11, 12, 14) | **yields**: the step ends, `through` unchanged, state stays `READING`; a refusal is **not** a failed search — it does not halve the span width and never creates a gap (§B.6.3) — because the cause is our own capacity, not the appliance's | the scope's `reason` gains "deferred: no Ariel permit within N s" and the search counter stays as it was |
| scheduled search (row 16) | a `FAILED` execution row with `error_type = CAPACITY` (a new member of `ExecutionErrorType` — the column is `String(64)`, `app/models/search.py` `SearchExecution.error_type`, so the value is additive with no migration) and `error_message` naming the wait and both caps. Not deferred: the scheduler has no defer state, and the next cron fire is already a coalescing retry (`SearchScheduler.run_due`) | **it counts toward `SEARCH_FAILURE_ALERT_AFTER`** — recommendation: yes, because a search that never runs is precisely the quiet blind spot that streak exists to surface, and the alert text carries the reason so the operator can raise the cap or fix the contention (§P.10 Q3) |
| manual execute (row 17) | the execution row is written `FAILED/CAPACITY` (the attempt is the audit trail) and the route answers **`503`** with a detail that names the bounded wait and both caps and says plainly that nothing was searched. 503, not 429: the ceiling is our own capacity policy, not a rate limit on the caller | `detail`: "the Ariel search budget is full (N of G in flight); waited 30 s; nothing was sent to QRadar" — counts only, no query text |
| PW preview (row 18) | `503` with the same shape; the two-search budget is untouched | as above |
| profile validation (rows 5, 9) | the probe records the error class in its existing per-field `error` slots (`WafPropertyProbe.error`, `IpsFieldProbe.error`); a profile that measured nothing can never come back `VALID` — validation already fails closed on an unmeasured probe, and the build task pins that a refusal never validates | the class name in the validation report |
| CLI (`sync`, `waf validate`, `waf probe-rejection`) | prints the typed message, non-zero exit | as the message |

**Detector honesty.** A refused tick writes no row at all, so every engine that reads the
families' tables sees absence, not zero: Phase A's zero-fill only runs for buckets a completed
search covered (`MetricCollector._zero_filled_rows`), and the WAF/IPS/catch-all folds are never
reached. Where a detector judges, absence already yields `INSUFFICIENT_DATA` — the permit design
adds no verdict path, it only makes "the appliance was never asked" visible and named.

## P.5 Defaults and capacity

### P.5.1 Do 3 per instance / 6 global still fit?

Search **demand** per 5-minute tick, using the family shapes of [`HISTORY-BACKFILL.md`](HISTORY-BACKFILL.md)
§B.8.1 and the code's own caps (§P.1.1; splits excluded — they are the exception, and the
16-search tick cap bounds them):

| Family | Small (1 console, 20 sources) | Typical (+3 WAF, +5 IPS sources) | Large (1 000 sources, 20 WAF, 30 IPS) |
|---|---:|---:|---:|
| Phase A + catch-all + geo (all on by default) | 4 / tick | 4 / tick | 4 / tick |
| explanations, burst only | 0 (≤ 100 with 5 pending anomalies) | as small | as small |
| WAF (2 searches/source, breakdowns off; 15-min tick) | — | 6 / 15 min | 40 / 15 min |
| IPS (≤ 16 shared; 15-min tick) | — | ≤ 16 / 15 min | ≤ 16 / 15 min |
| scheduled searches (≤ 25 enabled, crons ≥ 5 min) | ≤ 5 / tick average | as small | as small |
| history backfill while reading | ≤ 10 × families reading / tick | as small | as small |
| **steady total per 5 min (estimate)** | **≈ 4–9** | **≈ 11** (4 + (6+16)/3) | **≈ 23** (4 + (40+16)/3) |

Search **duration**, measured on the lab (§B.8.1; the repository's own numbers, not invented):
0.03–0.13 s for one bucket, 0.37–3.5 s for keyed windows, about 0.06 s per hour of span on that
console, with the 120-second poll loop as the hard ceiling per search.

At `G = 6` concurrent and a pessimistic 3.5 s average, the pool clears ~103 searches/minute —
two orders of magnitude above the steady demand of any shape above (the large estate's ≈ 23 per
5 minutes is ≈ 4.6/minute). Only at the pathological 120-second ceiling would the pool clear
~3/minute and fall behind a large estate — and that ceiling is a bound, not an expectation: the
measured durations are three orders below it. Should a console actually behave that way, the
bounded wait defers background work first (§P.2.5) and every refusal is named (§P.4), so the
failure is visible, not silent. The explanation burst (100 sequential searches ≈ 1–6 minutes of
one task holding **one** slot) is a tick-budget question, not a concurrency one.

**Recommendation: keep 3 and 6.** They were never derived from the appliance — no document in
this repository states an Ariel concurrency limit for QRadar; [`PHASE-F-CUSTOM-DETECTIONS.md`](PHASE-F-CUSTOM-DETECTIONS.md)
§F.8.2 only restates these same two settings as "the existing executor limits". The permit makes
them true; whether the appliance is comfortable at 6 in flight is a console fact **[C]** that the
lab validation must measure (§P.9, AP-8) before anyone raises them.

### P.5.2 New settings

| Setting | Default | Bounds | Doc row |
|---|---:|---|---|
| `ariel_permit_lock_namespace` | 4713 | pairwise distinct from 4711/4712/4714 (validator) | `docs/CONFIGURATION.md` "Ariel guardrails" |
| `ariel_permit_instance_lock_namespace` | 4714 | as above | as above |
| `ariel_permit_wait_seconds` | 30 | `0..600` | as above |
| `ariel_permit_poll_seconds` | 1 | `>0..30` | as above |
| `ariel_permit_reserved_live_slots` | 2 | `0..G−1` (validator against `ariel_global_max_concurrent_searches`) | as above, one sentence: "global slots only live collection may take" |

`ARIEL_MAX_CONCURRENT_SEARCHES` and `ARIEL_GLOBAL_MAX_CONCURRENT_SEARCHES` keep their names and
defaults; their CONFIGURATION.md rows are corrected to say what they now are ("concurrent Ariel
searches per console / in total, across every process, enforced by advisory-lock permits").
The WAF settings (`waf_ariel_permit_*`, `waf_ariel_max_concurrent_sources`) are unchanged.
No migration: settings only, no tables.

## P.6 Connections

Each held permit holds **one** PostgreSQL connection: the dedicated `AUTOCOMMIT` connection of
§P.2.2, taken from the process engine pool (`app/core/database.py` `get_engine`,
`pool_size=10, max_overflow=5` per process) and returned at release.

- **Per process, at most two permits are ever held at once.** A collector task holds one (its
  searches are sequential); a WAF task holds one WAF source permit plus at most one inner Ariel
  permit. The API process holds at most one per in-flight searching *request* — and the global
  pool itself caps the total across all processes at `G`.
- **The pool, not a new ceiling.** Permits share the engine pool with sessions; they occupy pool
  slots for the permit's duration (seconds to the search's bound) instead of a transaction's.
  Worst case at the recommended caps: 4 worker processes × ≤ 2 + API ≤ 6 ≈ **14** permit
  connections fleet-wide, inside the pools those processes already own (4 × 15 + 15).
- **`max_connections`.** The repository's PostgreSQL image sets no override
  (`deploy/postgres/`), so the PostgreSQL 16 default of 100 applies. Steady connections are far
  below it (backend + 4 workers + beat + a transient CLI or `migrate`); permits add no new
  processes and no new pool — they only change how long a pool connection is held. Sharing one
  engine per process (not one connection per permit for the process lifetime) is exactly what
  `WafArielBudget.for_session` already does, and is kept.

## P.7 Observability

The operator must be able to answer two questions: **how full is the pool right now**, and **is
collection waiting on it**.

| Surface | What it shows | Where it goes |
|---|---|---|
| `doctor` | a new in-container check `ariel_permits`: `pg_locks` counts per namespace (the generalisation of `WafArielBudget.active_permits`), printed as `held/cap` for global and per console; PASS normally, WARN when the global pool is saturated at sample time (`held >= cap`) | `app/cli/doctor.py`, one row in `_CHECK_RUNBOOKS`, runbook `OPS-R63` |
| admin status | `GET /api/v1/searches/ariel-budget` (new route, the shape of `app/api/routes/waf.py` `get_waf_ariel_budget`): enforced, caps, reserved slots, and permits held now, global and per console — counts only, no query text, as `WafArielBudgetOut` already promises. Refused-permit counters are deliberately not included (a counter nobody resets tells the operator nothing); waits are visible in logs and watermarks instead | new `app/services/ariel_status.py`, `app/schemas/` one model, one route |
| logs | one INFO line per acquisition that waited (`waited_seconds`, `class`); one WARNING per refusal, per §P.4; the log lines carry no family: the permit does not know the family, and the watermark and the history reason name it | `app/services/ariel_permit.py` |
| watermarks | collectors keep their existing `last_error`; WAF keeps `last_run_permit_wait_ms` — no new per-family metadata is introduced by this design | unchanged |
| support bundle | nothing new to add: the bundle already captures `doctor` output, so the new check reaches it (and OPERATIONS.md's `support-bundle` section needs no change) | `qradar-dash support-bundle` |

The existing `GET /waf/ariel-budget` route and `waf_ariel_budget_status` stay: they report the
WAF *source* budget, which is a different cap (§P.2.4). No frontend page is built in this
design: neither Ariel endpoint has a UI consumer today, and the operator surface for this is
`doctor` plus the admin status API (a UI tile can follow when a page asks for it).

**`docs/OPERATIONS.md` sections to change** (listed, not written — the build task writes them):
`Tanı` → the `doctor` container-check table (one `ariel_permits` row); `Belirtiler` → a new
`OPS-R63` entry ("a family is late and `last_error` says `ArielPermitUnavailable`", with the
raise-cap-or-lower-load remedies); `Geçmiş okuma` → `Sınırlar` (history yields when the pool is
busy; the reason string); `Kurulum` → nothing (no new installer question, no new wizard key).

## P.8 Dead code and documentation

| Piece | Decision | Reason |
|---|---|---|
| `RedisConcurrencyLimiter` (`app/services/concurrency.py`) | **delete** | zero callers (F5); under steady load it leaks a crashed worker's slot forever (every `INCR` refreshes the shared key's TTL, so the key never expires and the DECR never comes), and it raises instead of waiting. No test imports it |
| `ConcurrencyLimiter` (the ABC) | **delete with it** | its one surviving implementor is test-only once the executor switches |
| `InMemoryConcurrencyLimiter` | **delete**, and `tests/unit/test_concurrency.py` is replaced by the permit's own tests | after `SearchExecutor` stops taking a limiter it has no production caller; its semantics (blocking semaphores, process-local) are exactly what the permit replaces. The executor/scheduler tests that build it (`tests/integration/test_search_executor.py`, `test_search_scheduler.py`) move to a counting fake of the permit source (unit) and to the PostgreSQL permit suite (integration) — the concurrency behaviour they pinned stays pinned, against the real mechanism |
| `ARIEL_MAX_CONCURRENT_SEARCHES` / `ARIEL_GLOBAL_MAX_CONCURRENT_SEARCHES` | **keep**, meaning corrected | the caps become real; `.env.example` rows updated to say "enforced across every process by advisory-lock permits" |
| `WafArielBudget`, `waf_ariel_*` settings, `/waf/ariel-budget` | **keep unchanged** | §P.2.4 |
| `docs/CONFIGURATION.md` "Ariel guardrails" table | corrected rows + the five new rows | §P.5.2 |

## P.9 The build

Sizes: S under half a day, M about a day, L two days. Order: AP-1 → AP-2 → AP-3 and AP-4 in
parallel → AP-5 → AP-6 and AP-7 in parallel → AP-8. **Expected migration needs: none** (no
tables, no columns; the one new enum value `ExecutionErrorType.CAPACITY` lands in a `String(64)`
column).

| ID | Task | Size | Files | Depends on | Tests it pins |
|---|---|---|---|---|---|
| **AP-1** | The budget: `ArielPermitBudget` (both pools, derived instance ranges, bounded wait, reserved slots, `background_priority()`, `ArielPermitUnavailable`, `active_permits` counts) | M | new `backend/app/services/ariel_permit.py`, `tests/unit/test_ariel_permit.py`, `tests/integration/test_ariel_permit_budget.py` | none | unit: unbounded on a non-PostgreSQL engine; the contextvar is scope-local and always restored. Integration (PostgreSQL, the shape of `tests/integration/test_waf_ariel_budget.py`, one engine per holder): mutual exclusion across connections; bounded wait returns `None`; crash-safe (closing the connection mid-hold frees both slots); instance ranges of two consoles are disjoint; a forced base collision shares but never exceeds; background takes no slot `< R`; global never exceeds `G`; namespaces distinct from 4711/4712 |
| **AP-2** | Settings and injection: the five settings with validators (pairwise-distinct namespaces, `R < G`), `permit` parameter on `QRadarRestProvider`, both factories pass a budget (`instance_id=None` for the process-wide one), `.env.example` and CONFIGURATION.md rows | M | `app/core/config.py`, `app/providers/qradar_rest.py`, `app/providers/factory.py`, `.env.example`, `docs/CONFIGURATION.md` | AP-1 | validators refuse every collision; the factory passes a budget for REST and nothing for mock/MCP; a provider built without a permit runs unenforced (existing provider unit tests unchanged, zero-edit) |
| **AP-3** | Provider enforcement: the four lifecycle methods open `permit()`; the typed error on refusal; **the call-site guard** | M | `app/providers/qradar_rest.py`, new `tests/unit/test_ariel_permit_call_sites.py` | AP-1, AP-2 | (a) the guard: a source scan in the shape of `tests/unit/test_history_readers_are_classified.py` — every `create_ariel_search(` occurrence in `backend/app` must be the definition, one of the four provider lifecycle methods, or `SearchExecutor._run_once`, **and** each of the four methods must open the permit context manager; a new call site anywhere else fails the test; (b) behavioural: with an `httpx.MockTransport` provider and a refusing fake budget, every lifecycle method raises `ArielPermitUnavailable` and issues **zero** POSTs to `/ariel/searches` (fail closed before the wire); with a granting fake, exactly one acquisition per lifecycle |
| **AP-4** | The executor: `permit()` replaces `limiter.slot()` in `_run_once` (create → poll → results → cancel inside the permit); `run_due_searches` and `run_search` build the budget from their session; `ExecutionErrorType.CAPACITY` + `classify`; `ArielPermitUnavailable` non-retryable; the manual route's 503; scheduled runs count toward the failure streak | M | `app/services/search_executor.py`, `app/services/ariel_errors.py`, `app/models/enums.py`, `app/workers/tasks.py`, `app/api/routes/searches.py`, `tests/integration/test_search_executor.py`, `test_search_scheduler.py` | AP-1, AP-2 | a refused permit ends the attempt FAILED/CAPACITY with no retry (non-retryable set) and nothing sent to QRadar; the manual 503 detail names the wait and caps; a scheduling cycle with a refusing budget records FAILED for every due search and the streak alert fires at 3; a granting budget runs unchanged (the existing executor tests, on the fake) |
| **AP-5** | Priority wiring and the history yield: `background_priority()` around every history driver's provider calls (`app/history/` families and the WAF/IPS/catch-all/rule-match/PW step drivers), `run_preview`, the two profile-validation services, the WAF CLI probes | M | `app/history/families.py`, `app/collectors/{waf_endpoint_collector,ips_collector,catchall_collector,rule_match_collector,pw_collector}.py` (history paths only), `app/services/property_watches.py`, `app/services/{waf_profiles,ips_profiles}.py`, `app/cli/waf.py` | AP-1, AP-3, AP-4 | a history step whose provider refuses yields: `through` unchanged, no width halving, no gap, state `READING`, reason recorded; a live pass refuses and fails closed (watermark, `last_error`, cadence outcome `failed`); each wrapped caller is background (unit: the contextvar is set for exactly the provider calls) |
| **AP-6** | Observability: `doctor` check `ariel_permits` + runbook row, the admin status route and schema, the wait/refusal log lines, `docs/OPERATIONS.md` sections of §P.7 | M | `app/cli/doctor.py`, `docs/OPERATIONS.md`, new `app/services/ariel_status.py`, `app/schemas/ariel.py`, `app/api/routes/searches.py` (or a small new router), `app/services/ariel_permit.py` | AP-1 | the check prints `held/cap` and WARNs at saturation; the route returns counts only (no query text — assert no AQL fragment can appear); the OPERATIONS.md rows exist for each listed section |
| **AP-7** | Dead code: delete `app/services/concurrency.py`, port the executor/scheduler tests off `InMemoryConcurrencyLimiter` (the behaviour pins move per §P.8), `.env.example` wording | S | the files of §P.8 | AP-4 | `ruff check app tests` clean; `mypy app` clean; no reference to `ConcurrencyLimiter` remains in `backend/app`; the ported tests still fail when the concurrency guard they pin is broken on purpose |
| **AP-8** | **Lab validation** (read-only): run the stack against the lab with the default caps during a history backfill plus live ticks; sample the status route and `pg_locks` every second; assert held ≤ `G` global and ≤ `P` per console at every sample; `kill -9` a worker mid-search and observe the slot free (connection drop); record search durations at the observed concurrency to answer whether 6 is comfortable for the appliance **[C]**. Throwaway sampling script outside the repository | M | none; a report | AP-1..AP-7 | the report states each measured number and the verdict on the defaults |

The lab probe answers the one console fact this design cannot decide (§P.5.1): the appliance's
tolerance at 6 concurrent searches. If the console shows queueing or slowdown at 6, the remedy
is a lower `G` — a setting — not a design change.

## P.10 Open questions for the user

**Q1. Keep 3 per console / 6 global once they bind?** Recommendation: **yes** (§P.5.1). Nothing
in the repository ties these numbers to the appliance; the permit finally enforces them, and the
lab run (AP-8) is the honest way to revisit them.

**Q2. Reserved live slots = 2 of 6?** Recommendation: **yes**. It leaves background work 4 slots
— enough for a history backfill to finish inside its 72-hour budget on the estates of §B.8 —
while guaranteeing live collection 2. `R = 0` would let a backfill starve collection exactly when
the console is busiest; `R ≥ 3` halves history's throughput for no measured need.

**Q3. Does a capacity-failed scheduled run count toward `SEARCH_FAILURE_ALERT_AFTER`?**
Recommendation: **yes** (§P.4). The streak alert is the platform's "this search quietly stopped"
tripwire; excluding capacity would hide exactly that. The alert message carries the reason, so
the operator can tell contention from breakage.

**Q4. Manual run: keep the FAILED execution row and answer 503?** Recommendation: **yes**: the
row is the audit of an attempt that occupied a slot budget decision, and 503 with counts tells
the analyst precisely what to do (retry shortly). The alternative (no row) makes capacity
invisible in the search's own history.

**Q5. Fold `WafArielBudget` onto the new module's slot scan?** Recommendation: **not in this
build** (§P.2.4). WAF is built, reviewed and lab-validated; the fold saves ~40 lines and risks a
regression in the one budget that works today. Revisit after AP-8.

**Q6. Are profile validation, the rejection probe and the PW preview background class?**
Recommendation: **yes** (§P.2.5). They are operator-triggered and retryable; a collector tick is
not. The cost is that a validation may wait up to 30 s during a backfill — visible in its
report, honest, and rare.

**Q7. Raise the worker concurrency?** Out of scope here, but the permit makes it safe to try:
with enforcement in place, `--concurrency=8` cannot put more than `G` searches on the appliance.
Recommendation: leave 4 until AP-8 has measured the console at 6.
