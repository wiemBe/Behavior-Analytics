# Phase E — rule analytics (goal G3)

Status: **design, 2026-10-12**. Written by the orchestrator. It rests on the live facts measured
in `tasks/T-015.report.md` §8–§11, and serves goal G3 in
`PLANNER.md` §0.

## E.0 What the user asked for

- See which rules fire, and how much.
- See how much EPS each rule consumes.
- See how many rules are active and how many passive, plus every other rule statistic.
- Later, give the statistics with each rule's context to the internal AI for rule analysis.

## E.1 Facts that constrain the design (T-015, lab QRadar 7.6)

- **The rule inventory** (`/analytics/rules`, `/analytics/building_blocks`) has 14 keys:
  140 rules and 219 building blocks.
  - Types: EVENT, COMMON, FLOW and OFFENSE.
  - Origins: SYSTEM and USER (the API also documents OVERRIDE).
  - Rules and building blocks share one id space.
- **Capacity fields** (`average_capacity`, `base_capacity`, `base_host_id`,
  `capacity_timestamp`):
  - present on every row;
  - in millions of "EPS";
  - static, with a two-day-old timestamp;
  - zero for every disabled rule.

  They are QRadar's **benchmark of how fast a rule could be evaluated** (lower = costlier). They
  are **not** a firing rate, and must never be shown as one.
- **No rule-statistics endpoint, and no GET that returns rule logic (tests or XML).**
  `rules_offense_contributions` and `top_rules` describe offenses, not matches.
  `GET /analytics/rules/{id}/dependents` starts a server-side task: it is **banned**.
- **Firing counts are measurable.**
  - `SELECT creEventList, COUNT(*) FROM events GROUP BY creEventList` returns one row per
    rule-or-building-block id. The list is expanded, so an event counts once per rule it
    matches.
  - It is accepted, and takes about 1–2 s for 60 minutes in the lab.
  - A second key works too (`creEventList, logsourceid`).
  - `RULENAME()` returns `"null"` for many ids. Names must be joined by id from REST.
  - **Not measured:** the cost on a busy console. A 20k-EPS console scans about 72M events an
    hour.

## E.2 Measured rule matches (E.1a) [DECISION]

**The collector.** `rule_match`, one Ariel search per **window** per instance:
- default window 3,600 s;
- window-aligned, with a settle lag;
- watermark-driven, with bounded catch-up per run.

The AQL:

```
SELECT creEventList AS rule_id, COUNT(*) AS records, SUM(eventcount) AS events
FROM events GROUP BY creEventList ORDER BY records DESC
LIMIT <cap> START '<window start>' STOP '<window end>'
```

The AQL is stored and versioned, never built from user input (AGENTS.md rule 4). A full page,
`rows == cap`, makes the window **TRUNCATED**.

**Storage.** A migration adds:
- `rule_match_metric`, a hypertable:
  - PK `(rule_id, bucket_start)`;
  - `bucket_seconds`, `match_records`, `matched_events`;
  - sparse: a row exists only for a rule that matched.

  It is a separate table from `rule_metric`, whose PK would collide with the daily
  offense-contribution rows at midnight.
- `rule_match_window`:
  - PK `(instance_id, window_start)`;
  - `window_seconds`, `status` (COMPLETE / TRUNCATED / FAILED), `rows_returned`, `row_cap`,
    `unknown_ids` (ids that are in neither REST list), `duration_ms`, `query_version`,
    `collected_at`.

  This is what gives a rule's **absence** meaning, exactly as `waf_source_bucket` does for WAF
  endpoints.

**What a COMPLETE window proves depends on the rule type.** This lives in a pure function,
`match_evidence(rule_type)`:

| Rule type | An event search shows | Absence in a COMPLETE window means |
|---|---|---|
| EVENT, including EVENT building blocks | every match | **0 matches (a measured zero)** |
| COMMON | the event matches only | **unknown**: flow matches are not searched. The count is a lower bound. |
| FLOW | nothing | **not measured** |
| OFFENSE | nothing | **not measured** |

A flows search (`FROM flows`) would cover FLOW and COMMON. It is a later option behind its own
setting, not E.1a.

**Rule health.** `RuleHealthEvaluator` gains measured evidence:
- **EVENT rules:** observation is complete for the evaluation window iff every window in it is
  COMPLETE. The trigger count is then the measured sum, so `NEVER_OBSERVED`, `INACTIVE` and
  `NOISY` become provable.
- **COMMON rules:** measured matches are a lower bound. They can prove firing, never silence.
- **Everything else:** the existing behavior, unchanged (offense contribution, fail closed).
- The rule-health logic version is bumped. The fail-closed contract in the `classify` docstring
  stays.

**Cost and safety:**
- The setting `rule_match_collection_enabled` is **false by default**: cost on a busy console is
  unmeasured.
- The window and the row cap are settings.
- It goes through the existing Ariel lifecycle and concurrency controls.
- SQL per window is constant: one bulk upsert and one window row.

## E.3 Rule statistics and cost (E.1b) [DECISION]

An API, then a UI after T-021:
- counts by enabled/disabled × type × origin × building block;
- rules with no measured match over N complete days (silent);
- the top N by matches and by matched events;
- the top N **costliest by benchmark** (lowest `average_capacity`, zero excluded as "no
  benchmark"), labelled as QRadar's capacity benchmark;
- **match rate** (matches per second over the window), which is the honest answer to "how much
  EPS does this rule consume";
- capacity history, stored only when `capacity_timestamp` changes.

## E.4 AI rule analysis (E.3) — open decision D-5

The context package is the rule metadata, the measured statistics, the health and the
dependencies. **Rule logic cannot be read from QRadar over GET.** The options are:
- an operator-supplied offline export (QRadar's content-management export), parsed read-only;
- leaving the logic out.

This is the user's decision, and it is deferred until E.3.

## E.5 Task split

| Task | Agent | Content |
|---|---|---|
| T-025 | Sonnet | E.2: provider method, collector, migration, `match_evidence`, rule-health integration, Celery beat entry |
| later | — | E.3 statistics API, then UI; flows search; per-source breakdown (`creEventList, logsourceid`) |
