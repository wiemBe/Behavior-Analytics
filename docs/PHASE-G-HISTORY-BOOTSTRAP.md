# Phase G, history bootstrap: a watch reads its past from Ariel once, for learning only (D-27)

Status: **design, 2026-10-18** (PW-12). Not built. It extends
[`PHASE-G-PROPERTY-WATCH.md`](PHASE-G-PROPERTY-WATCH.md) (cited as §G.n) and changes none of its text:
the sentences this design makes wrong are listed in §H.0.4 for a later docs round (PW-11 is editing
that file now). It rests on [`SEARCH-CADENCE.md`](SEARCH-CADENCE.md) (§SC.5, §SC.6, §SC.10), on
[`PRODUCT-UX-GUIDELINES.md`](PRODUCT-UX-GUIDELINES.md) (§4, §6, §8) and on decisions D-27 and D-28
([`PLANNER.md`](PLANNER.md) §4). Every claim about the code carries a `file:line` citation checked
on this branch (`agent/pw12`, from `feat/phase-ce`) on 2026-10-18. Where the code and §G disagree the
code wins, and the disagreement is listed in §H.14.3. Facts that only the console can give are not
asserted: each has a probe in §H.3.5 and is marked **[C-n]**.

> **The user's decisions on §H.14.1 (2026-10-18 ~18:00, D-29; they override the text below where
> it differs).** Build it as designed, in the order of §H.13. **Q1: `pw_bootstrap_enabled` defaults to
> `true` everywhere** (the user chose this over the recommended "off in the code"), and so a watch
> created without `history_days` asks for `pw_bootstrap_days` when the switch is on (the API default
> is the setting, not NULL; `history_days: 0` asks for none). The fail-closed rules (§H.3.2 the
> floor, §H.6) are unchanged, and assumption A1 stays stated on the page. Q2: 28 days. Q3: new
> watches only, no prepending. Q4 to Q8: the recommendations as written.
> **D-30 (2026-10-18 ~21:20):** a property-watch baseline cell is reliable only when its retained
> samples come from at least `pw_baseline_min_visits` (2) different weekly visits. So a 7-day bootstrap
> leaves every cell unreliable (one visit each), and §H.8.2's "`D = 7` makes every cell reliable at
> once" no longer holds; the 28-day default gives four visits. The lab demonstration (§H.12) shows
> new-value detection, which does not need cells.

The numbers of the live run PW-10 are used only where this file says so. The report
(`QRadar-Dash-work/pw10/PW-10.report.md`) was still `STATUS: IN PROGRESS` when this was written; the
facts taken from it are its section 1 and 3 (scope, source 212, search times) and `probe.log`
(one 30-day search, 3.7 s).

---

## H.0 What the user asked, and what the bootstrap is not

User, 2026-10-18 about 17:15, after saying the machine cannot stay on for 24 hours: "Yes, design it
first". D-27 ([`PLANNER.md`](PLANNER.md) §4) states the decision:

> When a watch is enabled, it reads its past history from Ariel once, in bounded searches, **for
> learning only**: the bootstrapped buckets are never evaluated, never open an incident, an F.1
> breach or an alert. [...] A new watch detects from day 1, and the lab can show new-value detection
> in about an hour. The old buckets whose property did not parse are incomplete history (fail
> closed).

### H.0.1 What it is not

- **Not an evaluation of the past.** No detector, no lifecycle row, no F.1 rule judges a bootstrapped
  bucket. The bucket is evidence for later verdicts about live buckets, nothing more (§H.2).
- **Not an alert on old events.** The rule of [`SEARCH-CADENCE.md`](SEARCH-CADENCE.md) §SC.6 (lines 199-201,
  "an upgrade must not replay history into alerts") is kept in full. D-27 replaces only the words
  "replays no history" and "never backfills" of §G.6 and §G.15, which say the watch has no past; the
  rule behind them (history never reaches an alert) stays.
- **Not fabricated history.** A bucket is written only from a whole Ariel search that measured it.
  A span Ariel cannot be proved to hold, a failed chunk, a truncated one, a chunk whose property did
  not parse: each yields **no bucket** (or a bucket that says what it is), never a zero and never a
  `NORMAL`. The claim "this value was not seen before" gets weaker or disappears; it is never
  invented (AGENTS.md rule 8).
- **Not a second collector.** It is the live collector's own fold, admission and persistence run
  over older windows, in time order, so a bootstrapped bucket is byte-for-byte what live collection
  of that bucket would have written (§H.2.5). No new fold, no new admission rule.
- **Not a write to QRadar.** It sends the three existing read-only search kinds (GROUPED, TOTALS,
  PINNED) through `QRadarRestProvider.get_pw_keyed` (`backend/app/providers/qradar_rest.py:1662`),
  never through MCP, with no AQL from the browser (AGENTS.md rules 1, 2, 4).
- **Not raw storage.** Rule 3 is unchanged: value texts persist only as admitted or pinned
  `pw_value` rows; nothing of an un-admitted value is staged between ticks (§H.5.3 rejects a staging
  table for exactly this reason).

### H.0.2 The decisions in one table

| # | Decision | Where |
|---|---|---|
| H1 | A bucket read from history carries `pw_bucket.origin = 'HISTORY'`; live buckets are `'LIVE'`. Every reader that feeds a detector, the lifecycle, F.1 or an alert filters on it, and a guard test classifies every reader | §H.2.2 |
| H2 | While a watch is `READING` it has no `collected_through` and no `evaluated_through` (both NULL), so no existing query can see a bootstrapped bucket as pending; at hand-off both are set to the last history bucket | §H.2.3 |
| H3 | The history window is read **oldest first**, in bounded steps, one step per tick, and ends at the closed-bucket boundary of the tick that finishes it. The live collector takes over at the next bucket: zero gap, zero overlap | §H.2.4 |
| H4 | How far back Ariel holds the scope is **proved, not assumed**: a TOTALS scan finds the first bucket with events; nothing before it, nor before it plus a safety margin, is written | §H.3 |
| H5 | The searches are the existing GROUPED, TOTALS and PINNED kinds. TOTALS and PINNED run over spans of up to 168 h, GROUPED over density-sized sub-windows inside the span | §H.4 |
| H6 | Bootstrap searches draw on what is left of the tick budget after every live watch has had its turn, are capped separately, and stop after a time budget | §H.4.4 |
| H7 | Progress lives in columns on `property_watch` (additive migration `0029`); no new table | §H.5 |
| H8 | A kill switch, `pw_bootstrap_enabled`, **defaults to on** (D-29, the user's choice; the design proposed off until the probes of §H.3.5 were read) | §H.10 |
| H9 | An existing watch is not bootstrapped at upgrade. History can be requested only for a watch with no stored bucket of its current version | §H.5.5 |

### H.0.3 Alternatives weighed and rejected

| Alternative | Why not |
|---|---|
| **Fixed end, live from enable.** Live collection starts at the bucket of the first tick and runs in parallel while history fills behind it | Admission is order-dependent and stateful (`admit_bucket` "call this oldest first", `backend/app/property_watch/admission.py:149-153`). Live buckets would admit values before the history that makes them established; merging the two admission streams afterwards rewrites `first_seen_at`, `tracked_since` and the ceiling count of values already used by evaluated buckets |
| **Newest first**, so the 7-day novelty window completes soonest | Same ordering problem reversed: a value is "admitted" at its newest sighting and its older sub-threshold sightings become stored rows, which live would never have stored. The stored history would no longer be what live collection writes |
| **A staging table** of raw per-value aggregates, processed in order later | Persists un-admitted value texts, which rule 3 forbids (§G.7.1 "Never stored: a value that was not admitted") |
| **A fixed end at enable plus a catch-up burst of live buckets** | The buckets between enable and the end of a long bootstrap would be evaluated hours late, as a replay. The moving end of H3 makes them history instead; the cost is a blind period of minutes (small watches) to a few hours (§H.4.5), stated on the page |
| **Prepend history to an already-collected watch** | Its values and ceiling are already in use by evaluated buckets, the same conflict as the first row of this table. Not designed; §H.5.5 and Q3 |
| **Per-value top-N pre-pass** to choose which 200 values win the ceiling | A new query shape for a bias that the bucket flags already make honest (§H.7.2). Not needed |

### H.0.4 Sentences this design makes wrong (for the docs round, not edited here)

| File and line | Sentence | What it becomes |
|---|---|---|
| `docs/PHASE-G-PROPERTY-WATCH.md:530-533` (§G.6) | "the **first run seeds the watermark at the latest closed bucket and replays no history** [...] it learns forward and never backfills" | A watch without a history request still does this. A watch with one reads its past, learning only (this file); the SC.6 rule is the reason both are safe |
| `docs/PHASE-G-PROPERTY-WATCH.md:1921` (§G.17 limitations) | "No backfill: a watch learns forward from its first bucket (§G.6)." | "A watch learns forward unless history was requested (PHASE-G-HISTORY-BOOTSTRAP.md)" |
| `docs/PHASE-G-PROPERTY-WATCH.md:890-893` (§G.8.0) | "the first verdict on a cell comes **one week after the value was first tracked**" | True for a watch without history. After a bootstrap of D >= 7 days the cells are judged at hand-off (§H.8.2) |
| `docs/PHASE-G-PROPERTY-WATCH.md:1000-1002` (§G.8.3) | "Learning period: `pw_new_value_history_days` from the watch's first collected bucket" | The first collected bucket is the oldest history bucket, so the period is already over at hand-off if the window was read (§H.1.1) |
| `docs/PHASE-G-PROPERTY-WATCH.md:1485` (§G.13.4) and `frontend/src/components/propertywatch/authoring.ts:40-60` | "needs 7 days of history", "Needs a week of reliable baselines", "Needs 7 days of complete novelty history" | Conditional on the history option (§H.9.4) |
| `docs/PHASE-G-PROPERTY-WATCH.md:1608-1620` (§G.14.1) | "Per watch per tick the cost is `chunks x (2 or 3)`", the static cost at enable | Steady-state cost, still true. A bootstrapping watch also draws on the history budget of §H.4.4 |
| `backend/app/collectors/pw_collector.py:15-18` (module docstring) | "**The first run seeds the watermark at the latest closed bucket and replays nothing**" | Add: unless the watch is `READING` or `COMPLETE` history (§H.2.3) |
| `docs/SEARCH-CADENCE.md` §SC.5 item 2 | the window of one tick is at most `cadence_seconds / bucket_seconds + collection_max_backfill_intervals` buckets | A history step is a deliberate, budgeted exception (§H.4.2). The 168 h per-search bound (`backend/app/collectors/bucketed_metrics.py:28`) still holds |
| `README.md:1168` | "so an upgrade never replays history" | About F.1; stays true. The PW paragraph should say a bootstrapped bucket is not evaluated either |

---

## H.1 Per detector: what history it needs, what the bootstrap gives, what it still cannot know

The detectors are specified in §G.8 and built in `backend/app/property_watch/detectors.py`. The
bootstrap changes no detector. It supplies, earlier, the inputs they already require.

### H.1.1 The table

| Detector | Needs | The bootstrap gives it | What it still cannot know |
|---|---|---|---|
| **`PW_NEW_VALUE`** | For a value first admitted at `first`: every bucket of `[first - pw_new_value_history_days, first)` exists, with the same `watch_version`, none `NOT_COLLECTED`, none `LOWER_BOUND`, none with `admission_truncated` or `rejected_over_quota > 0` (`backend/app/property_watch/history.py:12-82`); and `first >= watch_first_collected_at + pw_new_value_history_days` (`backend/app/property_watch/detectors.py:598-612`), where that first-collected time is the oldest stored bucket of the version (`backend/app/repositories/property_watch.py:178-186`) | The whole window as history buckets from `F` (§H.3.1). With `D >= 7` days read and no hole, every first appearance **after hand-off** has a complete 7-day history, and the learning period is over at hand-off | Sightings below `pw_min_admission_events` (3 events in one bucket) were never stored, so "never seen" is still not claimed (§G.8.3). A bucket whose admission was truncated or whose search was cut blocks the claim for the 7 days after it. Events whose property was not parsed (§H.6, row 2). The scope as it was then (§H.6, row 4) |
| **`PW_NEW_COMBINATION`** | The tuple history above, plus, per component, an earlier counted occurrence (a presence claim, `LOWER_BOUND` suffices) (`backend/app/anomaly/capabilities.py:748-775`, `backend/app/repositories/property_watch.py:306-361`) | The component occurrences of the whole window: `component_occurrences` reads `pw_value_metric` joined to `pw_bucket` with no origin condition, and history is exactly the evidence it wants | Components seen only below the admission threshold are not known (§G.9.3); same limits as `PW_NEW_VALUE` |
| **`PW_VALUE_SPIKE`** | A reliable cell: `baseline_min_samples` (8, `backend/app/core/config.py:335`) clean samples in the weekday x hour cell | After training at hand-off, one visit of an hour-of-week gives up to 12 samples (§H.8.2). `D = 7` makes every cell reliable at once; `D = 28` gives four visits and up to 48 samples | A week-to-week variation that one visit cannot show (the samples of one visit are correlated). Values admitted late in the window have fewer visits |
| **`PW_VALUE_DROP`** | The same cell, `EXACT` counts, coverage at the floor | The same | The same; and it never reads a `LOWER_BOUND` bucket (a lower bound cannot prove a drop, §G.8.2) |
| **`PW_VALUE_DISAPPEARED`** | A cell median >= 5, then `pw_disappear_buckets` (6) consecutive **measured zeros** in evaluated buckets | The cell. The zeros must be live (§H.1.3) | Whether a gap in history was silence: only buckets after `F` are zeros (§H.3.2) |
| **F.1 threshold rules** on a pinned value | Nothing: "no learning period: a rule needs no baseline" (§G.8.4) | Nothing | See §H.1.2 |

### H.1.2 F.1 threshold rules do not read bootstrapped buckets

Decision: **no**. Two reasons:

1. A rule needs no history (§G.8.4), so there is nothing to gain; and a breach on a bucket read from
   the past is an alert on an old event, which D-27 and §SC.6 forbid.
2. The F.1 engine would otherwise read them by accident. Its PW loader reads every bucket of a
   pinned value in `(after, through]` with no condition on the watch's own watermarks
   (`backend/app/custom_rules/loaders.py:486-560`), and the engine keeps one watermark per subject
   *kind*, not per watch (`backend/app/custom_rules/engine.py:266-312`). A rule on watch B, or a
   rule created on watch A during its bootstrap, could therefore have the last history buckets of A
   fall after its kind watermark. So `load_pw_values` gets `PwBucket.origin == 'LIVE'` in both joins
   (§H.2.2, reader R5). The F.1 **preview** is a replay that persists and alerts nothing
   (`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.8.1), so it may read history: `load_pw_values` takes
   `include_history`, False by default, True from `custom_rules/preview.py` (§G.10.2 lists that
   reader).

### H.1.3 What a history bucket is, to a detector: evidence, never a judged interval

The engine reads recent buckets and history buckets to judge a **live** bucket: the trailing
`pw_disappear_buckets` readings (`backend/app/property_watch/engine.py:772-790`), the novelty history
(`engine.py:807-854`) and the component lookup. History buckets stay valid evidence for the second
and third. For the first, the streak that opens a disappearance incident begins at `first`, and the
incident's `interval_start` is that bucket (`detectors.py`, the disappearance verdict). An incident
whose interval starts in a bootstrapped bucket would be an incident opened from one. So:

- `_recent` takes only `origin = 'LIVE'` buckets. A disappearance can then open no earlier than
  `pw_disappear_buckets + anomaly_open_after_intervals - 1` live buckets after hand-off (7 buckets,
  35 minutes, at the defaults), which is the price of the rule.
- A value whose `first_seen_at` is before the first live bucket (`property_watch.history_end`) is
  **established**, whatever its age: `_established` (`engine.py:856-880`) is used for it, so a value
  that first appeared in the last hour of the window cannot be called new at hand-off. Its
  `first_seen_at` is a history time; only the live buckets after hand-off can raise a first
  appearance.

---

## H.2 Learning only

### H.2.1 The marker: provenance on the bucket

New column `pw_bucket.origin`, `String(8) NOT NULL`, `CHECK (origin IN ('LIVE', 'HISTORY'))`.
`'LIVE'` is every row written by the live path today; `'HISTORY'` is every row the bootstrap writes.
The bucket's `query_provenance` (`backend/app/models/property_watch.py`, `PwBucket`) additionally
records `{"history": true, "membership": "current", "floor": "<F>"}` for human reading; the column
is what code reads. The column is on the bucket, not the watch, because the watch's watermarks can
change meaning (a version bump, §H.5.4) while a stored bucket's provenance cannot.

A `HISTORY` row is always stored with `scope_changed = false` and `in_maintenance = false`. Both
describe the *scope at collection time*, which history does not have: today's maintenance state
applied to the past would exclude good buckets from training (`app/property_watch/baseline.py:92-113`)
and today's membership says nothing about the past (§H.6, row 4).

### H.2.2 Provably never read as a pending bucket: three independent guards and a classification

Guard 1, **watermarks**. While a watch is `READING` its `collected_through` and `evaluated_through`
stay NULL (§H.2.3). `pending_buckets` requires `PwBucket.bucket_start <= PropertyWatch.collected_through`
(`backend/app/repositories/property_watch.py:200`), which is not true against NULL, so no bucket of the
watch is pending. `due_baseline_values` requires `evaluated_through IS NOT NULL` (`:540`), so the
watch is not trained either until hand-off.

Guard 2, **the seed**. At hand-off both watermarks are set to the start of the last history bucket,
so the pending condition `bucket_start > evaluated_through` (`:201-204`) excludes every history
bucket whatever else happens to the watermarks later.

Guard 3, **the column**. `pending_buckets` adds `PwBucket.origin == 'LIVE'` inside the ranked
subquery, so even a watermark written wrongly (an operator's SQL, a future bug, a restored backup)
cannot make a history bucket pending.

A new test (`test_history_buckets_are_never_pending`) writes a `HISTORY` bucket with a start after
`evaluated_through` and with `evaluated_through` NULL, with `collected_through` set, and asserts the
pending read returns nothing, then breaks guard 3 on purpose and watches it fail (AGENTS.md §4).

**The classification.** Every reader of `pw_bucket` is one of three kinds, and a guard test
fails the build when a reader is unclassified, in the shape of `implemented_loaders()`
(`backend/app/custom_rules/loaders.py:41`, §G.10.2):

| # | Reader | Where | Kind | What it does with `HISTORY` |
|---|---|---|---|---|
| R1 | pending read for evaluation | `repositories/property_watch.py:170-229` | LIVE only | `origin = 'LIVE'` (guard 3) |
| R2 | `buckets_between`: the engine's history, recent readings and the training window | `repositories/property_watch.py:231-246`, used at `property_watch/engine.py:316` and `property_watch/training.py:76` | EVIDENCE | reads both; `_recent` filters to LIVE in Python (§H.1.3); training reads both on purpose (§H.8) |
| R3 | `component_occurrences` | `repositories/property_watch.py:306-361` | EVIDENCE | reads both |
| R4 | `scope_history`, the membership comparison | `repositories/property_watch.py:133-168` | EVIDENCE | reads both, so the first live bucket compares with the last history bucket |
| R5 | F.1 `load_pw_values` | `custom_rules/loaders.py:486-560` | LIVE only | `origin = 'LIVE'`; `include_history` for the preview only |
| R6 | the API: latest coverage, scope changes, buckets, series | `services/property_watches.py:578-612,700-720`, the bucket and series routes | DISPLAY | reads both and **labels** `origin` (§H.9) |
| R7 | retention | `services/pw_retention.py:113` | DELETE | by `bucket_start` as for any bucket |

Alerting never reads a bucket: it reads incidents, and an incident exists only through R1 and R5.
The live validation (§H.12) asserts this from the database: no `pw_anomaly`, `pw_anomaly_transition`,
`custom_rule_breach` or alert row refers to an interval before `history_end`.

### H.2.3 How `evaluated_through` and `collected_through` start

Per watch, in `property_watch`:

| Moment | `collected_through` | `evaluated_through` | `history_state` |
|---|---|---|---|
| created, never enabled | NULL | NULL | `NOT_REQUESTED` (no history) or `READING` once the first tick sees it |
| `READING`, any number of ticks | NULL | NULL | `READING`; `history_through` advances |
| hand-off (this tick writes the last history chunk) | start of the last history bucket `E - bucket` | the same | `COMPLETE`, `PARTIAL` or `UNAVAILABLE` |
| first live tick | `E - bucket` -> advances normally | `E - bucket` -> advances | unchanged |
| `UNAVAILABLE` (Ariel holds nothing of the scope in the window) | NULL (so the existing first-run seed runs) | NULL | `UNAVAILABLE` |

`collected_through` is "bucket start of the last collected bucket" and the live path starts at
`through + bucket` (`backend/app/collectors/pw_collector.py:356`). Setting it to the last history
bucket makes the first live bucket the one after it, with no new code in the live path.
`UNAVAILABLE` leaves NULL so the existing rule `start = boundary - step` (`:356`) seeds the live
path exactly as today.

### H.2.4 The hand-off: zero gap, zero overlap

The end of the history is **not fixed at enable**. Each step reads up to the closed-bucket boundary
of its own tick (`boundary = floor_to_interval(now - settle, interval)`,
`pw_collector.py:342-344`). The step that reaches `boundary` is the last one.

```text
history:  [ F ............. J ][ chunk of this tick ........ boundary )        <- HISTORY, origin='HISTORY'
live:                                                      [ boundary ....      <- LIVE, origin='LIVE'
          J = history_through (start of the last history bucket written)
```

- **Zero overlap.** The history chunk is `[J + bucket, e)` with `e <= boundary`. The live path starts
  at `collected_through + bucket`. At hand-off `collected_through = e - bucket`, so the first live
  bucket starts at `e = boundary`. Bucket starts are unique per watch (`pw_bucket` primary key
  `(watch_id, bucket_start)`, `models/property_watch.py:210-212`); an overlap would be an upsert onto
  an existing row, and the test asserts none occurs.
- **Zero gap.** The next live bucket starts where the last history bucket ended. If the final chunk
  completes only a prefix (a search failed or the budget ran out), `J` advances only past the
  contiguous completed prefix (the rule of `collected_prefix`, `pw_collector.py:291-312`) and the
  watch stays `READING`; the next tick continues at `J + bucket` against a newer boundary.
- **A tick that runs while the bootstrap is half done.** A `READING` watch is skipped by the live
  plan and planned by the history planner (§H.4.2). It writes only history chunks and moves only
  `history_through`. Another tick between two of its steps changes nothing: each step is read from
  the stored `history_through`, and its writes are idempotent upserts. A second tick *overlapping*
  the first (a tick longer than the cadence slot: `claim_slot` lets the next slot start,
  `backend/app/services/search_cadence.py:83-95`) is prevented by a per-watch
  `pg_try_advisory_xact_lock` taken before the first search of the step; a tick that does not get
  it skips the watch (the live dispatch has no such lock today: `workers/tasks.py:1394-1442`; a
  history step is long enough to need one). All writes of a step, including `history_through` and,
  on the last step, the two watermarks, are in the instance's one transaction
  (`workers/tasks.py:206-210`): a crash rolls the whole step back.
- **The moving end costs a blind period, not a hole.** The buckets between enable and hand-off are
  history, not live: nothing is evaluated for the watch in them. For a 6-value watch that is
  minutes; for a 200-value watch it can be hours (§H.4.5). The page says "reading history: n of m
  days" for as long as it lasts, and the alternative (a fixed end, a catch-up burst of live buckets
  evaluated late) was rejected in §H.0.3.

### H.2.5 Equivalence: a history bucket is what live collection would have written

The history chunk goes through the same functions as a live chunk: `run_search`,
`combine_chunks`, `collected_prefix`, `fold_bucket`, `admit_bucket`, `retire_stale`, `sync_pins`
(`pw_collector.py:523-721`), with these differences and no others:

| Live | History |
|---|---|
| `scope_changed_flags(history, ...)` | the same call, so a membership change *during* the bootstrap is flagged; `history` read from `scope_history` as for live (R4) |
| `in_maintenance = _scope_in_maintenance(scope, bucket_start)` (`:672`) | `False` (§H.2.1) |
| `retire_stale(values, reference=last bucket of the tick)` once per tick (`:693-694`) | once **per bucket** inside the chunk, so the ceiling and the tracking windows are what bucket-by-bucket live collection would have produced; in memory, no statement |
| `status_row["collected_through"] = reference` (`:711`) | `history_through = reference`; watermarks only at hand-off |
| `origin = 'LIVE'` | `origin = 'HISTORY'` |
| `NOT_APPLICABLE` check at the end of the tick (`:712-717`) | not run: nothing is judged |

A unit test runs the same scripted mock search (`MockQRadarProvider.pw_script`,
`backend/app/providers/mock.py:292-294,1180-1195`) once through the live path bucket by bucket and
once through the history path in chunks, and asserts the stored rows are equal column for column
except `origin`, `collected_at` and `query_provenance` (the SC.5 item 4 bar,
[`SEARCH-CADENCE.md`](SEARCH-CADENCE.md) §SC.5).

---

## H.3 The window

### H.3.1 How many days

| Setting | Meaning | Default | Bounds |
|---|---|---|---|
| `pw_bootstrap_days` | the window a watch asks for | **28** | `>= pw_new_value_history_days` (7) and `<= pw_baseline_lookback_days` (28) |
| per watch `history_days` | the days one watch asks for | none (not requested) | 0 or `pw_new_value_history_days` to `pw_bootstrap_days` |

Why 28: the volume detectors build their cells over `pw_baseline_lookback_days` = 28
(`backend/app/core/config.py:656`; training reads exactly that window, `property_watch/training.py:71-76`),
so reading more is wasted and reading less leaves fewer visits per cell (§H.8.2). Why not less: the
new-value detectors need 7 (`config.py:671`). Why a per-watch value: the lab wants 7 or 3, a
production watch 28; the cost is bounded by the tick budget either way (§H.4.4), so the objection to
per-watch overrides in §G.3 (one watch consuming the budget) does not apply.

Hard bounds:

- **`pw_metric_retention_days` = 35** (`config.py:675`). The daily retention task deletes buckets older
  than `now - 35 d` (`backend/app/services/pw_retention.py:113`). Window 28 d plus up to
  `pw_bootstrap_max_hours` (72 h) of reading stays below 35 d, with 4 days to spare. The validator
  `pw_bootstrap_days <= pw_baseline_lookback_days` inherits the existing `lookback <= retention`
  check (`config.py:931-935`), and `pw_new_value_history_days < retention` (`:936-940`) is unchanged.
- **Ariel's own retention**, below.
- **168 h per search.** `validate_bucketed_metric_window` refuses a window above 168 hours
  (`backend/app/collectors/bucketed_metrics.py:28`), so a span is at most 2,016 buckets.
- **120 s per search.** `_run_bounded_aggregate` polls 120 times at 1 s and then cancels
  (`backend/app/providers/qradar_rest.py:2110-2126`); a search that long ends `FAILED`
  (`keyed_search.py:264,270`). Wide windows must stay below it (§H.4.3, **[C-5]**).

### H.3.2 How the design knows, read-only, how far back Ariel holds this scope

**The question.** A TOTALS search returns no row for a bucket in which the scope has no event. That
is the measured silence of §G.6, **or** the bucket has aged out of Ariel's retention (or, for a
property, its payload has). The two are indistinguishable in one bucket. Writing "0 events, EXACT,
complete" for an aged-out bucket would be fabricated history: a value absent from it would look
absent from the world.

**The rule: a bucket is history only if it can be proved held; the proof is an event of the same
scope at or before it, plus a margin.**

1. **The scan.** The first step of a watch's bootstrap runs TOTALS (5-minute buckets, the existing
   kind) over `[T0 - m, T0 - m + 168 h)`, where `T0` is the start of the asked-for window (the
   boundary of the first tick minus `history_days`, bucket-aligned) and `m` is
   `pw_bootstrap_floor_margin_hours` (24). If that span has no event, the next 168 h span is scanned,
   and so on to the boundary. Each is one search; each is a `COMPLETED`, untruncated search, or it
   proves nothing (a failed or cut one is not evidence).
2. **The floor.** Let `b0` be the first bucket with `total_events > 0`. Then
   `F = max(T0, b0 + m)`, bucket-aligned. It is stored in `property_watch.history_floor`.
3. **Everything from `F` on is held-proved.** An event of the scope exists at `b0 <= F - m`; Ariel
   removes its oldest data first (assumption **A1**, **[C-2]**), so no bucket after `b0 + m` can have
   aged out while a bucket before it survived. A zero total after `F` is therefore measured silence,
   the same zero a live bucket stores.
4. **Nothing before `F` is written.** Not a zero, not a `NOT_COLLECTED` row. Absence of a row is "no
   claim" and `verify_value_history` already reports a missing bucket as an incomplete history
   (`history.py:34-41`). The watch page says "QRadar holds events of this scope from F; T0 was
   asked".
5. **No event in the whole window** (the scan reaches the boundary): `history_state = UNAVAILABLE`,
   nothing is written, hand-off is the existing first-run seed (§H.2.3).

What the rule costs: a silent scope loses its leading silence (history starts at its first event
plus `m`), which is conservative and true to what is provable. It adds **no search** to a scope whose
first window bucket already has events: the scan *is* the first TOTALS search of the first span, and
its result is used by that step's fold (§H.4.2).

**What the rule does not prove, and what covers it.**

| Assumption | If false | Covered by |
|---|---|---|
| A1: deletion is oldest-first within the scope's retention class **[C-2]** | A scope whose events fall in several retention buckets (QRadar retention buckets are filters) could keep a long class and a short one; the totals would reach back, the short class's values would be missing earlier, and a history window could look complete while missing a class | Probe P-3 reads the retention configuration; the page states the assumption; `pw_bootstrap_enabled` is off until P-3 and P-2 are read (§H.10). Not detectable at run time |
| A2: the counts of a bucket near the floor are whole **[C-4]** | The bucket containing the cut is partial, and a partial bucket would be stored as exact | the margin `m` (24 h by default); P-7 measures the ramp |
| A3: the property is extractable from old events **[C-3]** | Payload retention shorter than event retention, or an indexed (optimised) property extracted at ingest only: old events have the events but not the property | the coverage rule of §H.6, row 2: `parsed / total` is stored per bucket |
| A4: counts equal at search time and at original collection | a late event changes an old bucket | none needed: history is read once, the later live path never revisits it |

**Why not a console-wide witness at run time.** An unscoped search per bucket (events of the whole
console) would prove "Ariel holds that hour" without the scope's own event, but it scans the whole
console and still does not see the scope's retention class. P-2 uses it as a one-off cross-check;
the run-time rule above is the one that costs nothing extra.

### H.3.3 Where the proof is stored

`history_floor` (the proved start), `history_target_start` (`T0`), and the counts in
`history_progress` (§H.5.1). The watch page and the investigation view both print the floor.

### H.3.4 The asked days versus the proved days

`history_state`:

- `COMPLETE`: `F <= T0 + one bucket` (nothing was cut by retention) and no hole.
- `PARTIAL`: Ariel holds less than asked (`F > T0`), or a hole exists (§H.6), or the reading ended
  on a time or failure limit, or the switch went off. `history_reason` says which, in words.
- `UNAVAILABLE`: no event of the scope in the window, or the scan could not be done.

None of these three is an error, and none is health: `PARTIAL` and `UNAVAILABLE` are honesty states
(guidelines §6 `TRUNCATED` and `UNAVAILABLE`), and a detector reads them through the buckets they
left (missing buckets), not through the state.

### H.3.5 Probes: the console facts this design cannot assert

The orchestrator runs these as a separate read-only lab task (`PW-13p`, §H.13). A throwaway script
outside the repository, the REST provider, MCP never, sequential, aborting on the first timeout.
Use the lab's existing scopes (source 212 `ips-gw-01@IPS`, the ones PW-10 found; type 368 for volume).
At most **40 Ariel searches and 6 other GETs**.

| Probe | Request | What each answer means |
|---|---|---|
| **P-1 age of the scope** (central) | per candidate scope (212 and type 368): daily TOTALS over the last 35 days in 5 spans of 7 days, `SELECT starttime / 86400000 AS "k", SUM(eventcount) AS "events" FROM events WHERE <scope> AND starttime >= a AND starttime < b GROUP BY starttime / 86400000 ORDER BY SUM(eventcount) DESC LIMIT 8 START a STOP b` (10 searches) | Leading days with no row: the scope is young **or** aged out (**[C-1]**). The first day with events is `b0`. A demo needs `b0 + m` at least 7 days ago (§H.12) |
| **P-2 console-wide floor** | the same daily search with no scope condition over the same 35 days (5 searches) | If the console has events earlier than the scope's first day, the scope is young or silent there (not retention). If the console's own first day equals the scope's, the lab's retention is at that day. Distinguishes "aged out" from "never existed" for the lab |
| **P-3 retention configuration** | `GET /api/help/endpoints` filtered for "retention" (1 GET); if an endpoint lists retention buckets, one GET of it; else record that it is not exposed | The number of retention buckets and their filters answer A1 **[C-2]**. If none is readable, A1 rests on P-1 and P-2 and the setting stays off for production until the console owner confirms |
| **P-4 property on old events** | for 212 + `username`, and 212 + `IPS_Action`: daily COUNT of events, and of events with the property `IS NOT NULL`, over 35 days (10 searches) | The per-day coverage; a drop to 0 on older days is payload expiry, or the property being new **[C-3]**. Confirms the coverage rule of §H.6 is needed and at what floor |
| **P-5 time against window width** | grouped (5-minute key) on type 368 `"Metric ID"` and on 212 `username`, windows of 1 h, 6 h, 24 h, 72 h and 168 h, `LIMIT 5001` (10 searches) | Seconds per search and rows, so the cost table of §H.4.5 uses measured numbers **[C-5]**. A 168 h search near 120 s sets the sub-window ceiling |
| **P-6 repeated cost** | the 24 h window of P-5 repeated three times | Cold against warm; whether the first search of a process is the slow one (T-040 saw 3.5 s on the first) |
| **P-7 the ramp at the floor** | only if P-1 or P-2 finds a floor inside 35 days: hourly TOTALS for the 3 days around it (1-2 searches) | Whether the first day is partial **[C-4]**; sets `pw_bootstrap_floor_margin_hours`. If the lab has no floor in 35 days, record "not observable" and keep the 24 h default |
| **P-8 the 120 s edge** | the 168 h window on type 368 with the heaviest grouping, once | Whether `_run_bounded_aggregate` times out at the widest span; if it does, spans are capped lower |

Outputs: counts, seconds, row counts, dates; no value text enters the report. Each probe records its
AQL in the log.

---

## H.4 The searches

### H.4.1 The shapes: nothing new

| Kind | Query | History use |
|---|---|---|
| TOTALS | `SELECT starttime / <bucket_ms> AS "k", SUM(eventcount) ... GROUP BY k LIMIT <buckets + 1>` (`backend/app/property_watch/aql.py:150-156`) | the floor scan and the denominator, over a span of up to 2,016 buckets; its cap is the bucket count (`keyed_search.py:148-160`), so it never truncates |
| GROUPED | the keyed value query with `IS NOT NULL` on each property (`aql.py:157-170`) | over a **sub-window** sized by density (§H.4.3) |
| PINNED | the same restricted with `IN` (`aql.py:160-165`) | over the span, when values are pinned; cap `min(pinned rows per bucket x buckets, pw_max_rows_per_search)` |

Bucket width stays `collection_interval_seconds` (300 s). Nothing about the scope filter changes.
Every search carries the half-open `WHERE starttime >= a AND starttime < b` and the Ariel `START a STOP b+pad`
(`backend/app/providers/qradar_rest.py:292-312`, `:1662-1721`): the `WHERE` is authoritative, the pad
(`ariel_stop_pad_seconds`, 60 s, `config.py:136`) is capped at "now", so on a window days old it only
widens the scan by 60 s (C-6 asks the probe to confirm it does not move a count).

### H.4.2 A history step

One step per watch per tick, planned by a pure function (`property_watch/bootstrap.py`, PW-13b):

```text
inputs : state (T0, F or none, J or none), boundary, budget B, time budget, chunk_buckets W,
         pins, scope, settings
1. scan   if F is none: TOTALS over [T0 - m, +168h) (and further spans while empty, within B);
                         set F; if none by the boundary: UNAVAILABLE, stop.
2. span   [J + 1 bucket or F, s)  with  s = min(boundary,
              start + min(2016, pinned_cap_buckets, (B - reserved) * W) buckets)
3. run    TOTALS over the span          (reuse the scan's result if it covers the span)
          PINNED over the span          (when pins exist)
          GROUPED over consecutive sub-windows of W buckets
4. fold   the contiguous completed prefix, oldest first: fold_bucket, admit_bucket,
          retire_stale per bucket (the live functions)
5. write  buckets (origin HISTORY), values, metrics, the watch row
6. adapt  W := clamp(floor(0.8 * pw_max_rows_per_search / (1.25 * rows per non-silent bucket seen)),
                     12, 2016); halve W on a FAILED sub-window; keep it on success
7. finish if the prefix reached the boundary: set collected_through = evaluated_through =
          boundary - 1 bucket, history_end = boundary, history_state
```

`reserved` is 1 for TOTALS plus 1 when values are pinned. `pinned_cap_buckets` is
`floor(0.8 * pw_max_rows_per_search / pinned_rows_per_bucket)`
(`aql.py:127-133`), so a watch with ten pins reads 400-bucket spans. The first GROUPED sub-window is
288 buckets (one day) until a density is observed. The truncation split of `run_search` stays: a
truncated sub-window is split in time and charges the budget (`keyed_search.py:272-281`); the next
`W` is halved so it does not repeat.

**Failure is not truncation.** A `FAILED` search is not split in the live driver
(`keyed_search.py:264,270`). Here a failed sub-window increments `history_progress.failures` and
halves `W` for the next tick (a timeout is a function of width). After
`pw_bootstrap_max_failures` (3) consecutive failures at the minimum width (12 buckets), that
sub-window becomes a **gap** (recorded in `history_gaps`, no rows written, `PARTIAL`) and `J` moves
past it; novelty windows crossing it fail closed by their missing buckets.

### H.4.3 Chunking by rows

`values x buckets <= pw_max_rows_per_search` (5,000, `config.py:641`): the search returns one row
per distinct value per non-silent bucket, whether or not the fold later keeps it. The planner targets
80 % of the cap so an ordinary bucket does not truncate the window. A bucket with more distinct
values than the cap truncates even alone and is stored `LOWER_BOUND` (`keyed_search.py:280-281`),
which is useless as history (`history.py:61-64`). The **density gate** stops that waste: after the
first GROUPED sub-window, if more than `pw_bootstrap_max_lower_bound_share` (0.05) of the buckets
read came back `LOWER_BOUND`, the bootstrap ends `PARTIAL` with the reason "more than N distinct
values per 5-minute bucket: history would be lower bounds"; the buckets read stay (they are valid
evidence for the volume baselines of the values they name).

### H.4.4 The tick budget: live first

The plan of a tick (`dispatch_property_watch_collection`, `workers/tasks.py:1394-1442`) becomes:

1. every **live** watch is planned and run first, under the one `PwSearchBudget(pw_max_searches_per_tick)`
   exactly as today (`pw_collector.py:394-418`);
2. then every `READING` watch, oldest `history_started_at` first, under a sub-budget
   `min(pw_bootstrap_searches_per_tick, budget.remaining)`, and until `pw_bootstrap_seconds_per_tick`
   have passed since the tick started (no new search starts after it).

So a history step only ever spends what live collection left over, which keeps the one Ariel-load
knob of §G.14.1 and the concurrency limits (3 per instance, `config.py:122`) intact. The price: if
live watches use the whole budget, history starves, visibly (`history_reason = "waiting for search
budget"`). Enabling a watch **with** history is refused with 409 when fewer than 3 searches of the
tick budget are free after the static cost of the enabled watches (one triple: TOTALS, GROUPED,
PINNED), naming both numbers (the pattern of `enable_reasons`,
`backend/app/services/property_watches.py:448-492`).

### H.4.5 Cost table (design figures, not measurements)

Assumptions, each stated: 5-minute buckets; one pinned value, one property; `rho` is the number of
distinct values returned per non-silent bucket (6, or 200 as the worst case, the active ceiling);
sub-window `W = floor(4000 / rho)` capped at 2,016; TOTALS spans of 168 h with the 24 h margin day
(`ceil((D + 1) x 288 / 2016)`); PINNED spans of 2,016; budget 12 history searches per tick;
cadence 5 minutes (D-28). **Ariel time per search is the measured 1 to 3.5 s** (PW-0 L8, 1.1 s for
an hour at about 240,000 events; PW-10 section 3, 1.1 to 1.3 s; T-040, 2.2 s for 8 hours and 3.5 s
for the slowest, [`SEARCH-CADENCE.md`](SEARCH-CADENCE.md) §SC.10; PW-10 `probe.log`, 3.7 s for a 30-day
username aggregate with no bucket key). It is **unmeasured above 8 hours of grouped data with a
custom property [C-5]**; if a 168-hour search is much slower, the sub-windows shrink and the tick
counts below grow.

| Watch | Days | Sub-window W | Grouped | TOTALS | PINNED | Searches | Estimated Ariel time | Ticks to finish (12/tick) | At 5 min | At 1 h |
|---|---|---|---|---|---|---|---|---|---|---|
| 6 values | 1 | 666 | 1 | 1 | 1 | **3** | 3-10 s | 1 | 5 min | 1 h |
| 6 values | 7 | 666 | 4 | 2 | 1 | **7** | 7-25 s | 1 | 5 min | 1 h |
| 6 values | 28 | 666 | 13 | 5 | 4 | **22** | 22-77 s | 2 | 10 min | 2 h |
| 200 values | 1 | 20 | 15 | 1 | 1 | **17** | 17-60 s | 2 | 10 min | 2 h |
| 200 values | 7 | 20 | 101 | 2 | 1 | **104** | 1.7-6 min | 9 | 45 min | 9 h |
| 200 values | 28 | 20 | 404 | 5 | 4 | **413** | 7-24 min | 35 | 2 h 55 min | 35 h |

Add one tick for the hand-off (the boundary moves while reading) and, after it, one training tick per
100 values (§H.8.1). A watch without a pin has no PINNED column (searches minus 1, 1, 4). The 200-value
rows are a worst case (all 200 values present in every bucket); a real scope with fewer distinct
values per bucket reads wider sub-windows and finishes sooner. The figures follow the `rho` the first
sub-window observes, not a guess.

What the table says: a 6-value lab watch finishes in one or two ticks, which is the "about an hour"
demonstration with room to spare (§H.12). A 200-value watch for 28 days is a three-hour job at the
D-28 cadence and a day and a half at the old one-hour cadence, which is why the 5-minute default
matters (D-28) and why the page shows progress. Rows to write at the worst case: `200 x 8,064` =
1.6 million metric rows per watch (the §G.14.2 worst case, now reached at day 1, not day 28); each
tick writes them by `executemany` in one statement per table (§H.5.6).

### H.4.6 Cadence

D-28 makes 5 minutes the default for the family (the code seeds 1 h today:
`backend/alembic/versions/0026_property_watch.py:258`, until T-134). The bootstrap advances once per
family slot (`claim_slot`, `search_cadence.py:61`): it does not add a task or search outside the
cadence, so the operator's one control over Ariel schedule (D-7) holds. A longer cadence slows the
bootstrap in proportion (table). A faster bootstrap than one step per slot is Q6.

---

## H.5 State

### H.5.1 New columns on `property_watch` (migration `0029_pw_history_bootstrap`)

| Column | Type | Meaning |
|---|---|---|
| `history_days` | `Integer NULL` | days requested; NULL = no history requested |
| `history_state` | `String(16) NOT NULL`, `CHECK IN ('NOT_REQUESTED','READING','COMPLETE','PARTIAL','UNAVAILABLE')` | §H.3.4 |
| `history_target_start` | `DateTime(tz) NULL` | `T0`, fixed at the first step |
| `history_floor` | `DateTime(tz) NULL` | `F`, the proved start (§H.3.2) |
| `history_through` | `DateTime(tz) NULL` | start of the last history bucket written (the resume point) |
| `history_end` | `DateTime(tz) NULL` | the first live bucket start, set at hand-off |
| `history_started_at`, `history_finished_at` | `DateTime(tz) NULL` | wall clock |
| `history_progress` | `JSONB NOT NULL` default `{}` | `chunk_buckets`, `failures`, `searches`, `buckets_read`, `gaps` (at most 20 `{start, end, reason}`), `reason` (words, never a value text) |

`pw_bucket` gains `origin` (§H.2.1). `last_status_known` (`models/property_watch.py:105`) gains
`READING_HISTORY` (a new `PwWatchStatus`, `models/enums.py:263`), owned by collection: it is set by
the history planner and cleared at hand-off the way `_COLLECTION_STATUSES` clears
(`pw_collector.py:83-90`). The engine never sees it (a `READING` watch has no pending bucket); the
set `_DETECTOR_OWNED` (`engine.py:202`) is unchanged.

`history_progress` is JSONB, not columns, for the counters and the gap list: they are read only by
the API and the planner, never filtered or joined.

### H.5.2 Progress that resumes after a failure or a restart

`history_through` and `history_progress` are written in the same transaction as the buckets
(`pw_collector.py` `_persist`), so they are exactly as far as the stored rows. A tick that fails
(provider error, worker restart, rollback) writes nothing and the next one re-plans from
`history_through`. A restart of the Celery worker mid-step is the same case. Re-running a step is
idempotent: `write_buckets` and `write_metrics` are upserts keyed `(watch_id, bucket_start)` and
`(pw_value_id, bucket_start)` (`repositories/property_watch.py:389-418`), `write_values` keeps the
earliest `first_seen_at` (`:362-387`). The admission state for a step is the stored `pw_value` rows
(the bulk read at `pw_collector.py:382-384`), so a step starts from the state the last committed
step left.

### H.5.3 No staging table

The progress of a bootstrap is one watermark per watch. The aggregates a step reads are folded,
admitted and written in the same transaction; anything not admitted is gone with the transaction, as
in the live path (§G.7.1). A staging table of raw aggregates (to process newest-first, or in a second
pass) would persist value texts rule 3 forbids, and was rejected for that reason (§H.0.3).

### H.5.4 What an edit, a disable, a delete, a scope change and a re-sync do

| Event | Effect on a bootstrap in progress | Why |
|---|---|---|
| **Edit that bumps `watch_version`** (scope or properties, `api/routes/property_watches.py:826-828`) | The bootstrap **restarts** for the new version if `history_days` is set: `history_state = READING`, `history_*` cleared, `collected_through = NULL`, `evaluated_through = NULL`. Old-version values retire as today (`pw_collector.py:645-646`). The new history overwrites old-version bucket rows at the same `bucket_start` (the primary key has no version, `models/property_watch.py:210-212`), which nothing reads (every reader filters on the current version) | Values and baselines of another version are never compared (§G.3); the new version needs its own history. The patch route does **not** reset watermarks for a non-bootstrap watch today, and that is left alone |
| **Edit of pins, severity, detectors, name** | Nothing. A new pin is `sync_pins`-ed at the next step's first bucket, so its `tracked_since` is that bucket, not `F`: it has no measured zeros before it (`admission.py:105-130`, honest and stated) | no version bump (§G.3) |
| **Disable** | The watch leaves `enabled_watches`, so no step runs; `history_*` is kept. Re-enable resumes at `history_through` against the then boundary | the end moves, so a long pause only lengthens the last step. The wall-clock limit `pw_bootstrap_max_hours` (72) counts from `history_started_at` and so includes the pause; past it the next step hands off `PARTIAL` with the reason, so a half-aged window never resumes |
| **Delete** | Cascades (every bucket, value, metric, baseline: `ondelete="CASCADE"`, `models/property_watch.py:209-212,267,301`). A step in flight fails at its write with a foreign-key error and rolls back its instance transaction (the same exposure as a live tick, longer here: **open point**) | the routes lock the watch row (`_locked`, `routes/property_watches.py:177-183`) but the collector does not |
| **Scope change that keeps the version** | There is none: a scope change bumps the version (`routes:826`) | |
| **Inventory re-sync** changes a group's membership | `scope_changed_flags` flags the first history bucket under the new fingerprint and the grace buckets after it (`property_watch/scope.py:248-281`), as in live. `SCOPE_STALE`, `SCOPE_EMPTY`, `SCOPE_TOO_LARGE` stop the step like a live tick: no search, status set, `history_through` unmoved (`pw_collector.py:465-471`) | the same resolver, read every tick |
| **Kill switch turned off** | A `READING` watch hands off at once with `PARTIAL`, reason "history reading was switched off", leaving the buckets read and a gap to the boundary | the operator wanted no more Ariel load, not a watch that never starts |
| **`pw_collection_enabled` off** | The dispatch is a no-op (`workers/tasks.py:1402-1404`); nothing moves | |

### H.5.5 An existing enabled watch at upgrade

**Decision: no bootstrap at upgrade.** Migration `0029` sets `history_state = 'NOT_REQUESTED'`,
`history_days` NULL and `origin = 'LIVE'` for every existing row; an existing watch keeps learning
forward, exactly as before. Why:

- Its values, ceiling and bucket flags are already in use by evaluated buckets. Reading older history
  into it is the prepend problem (§H.0.3): admission is order-dependent and `first_seen_at` of its
  values would have to move earlier under incidents already open on them.
- An unrequested Ariel read on every enabled watch at the moment of an upgrade is exactly the burst
  an upgrade should not cause.

History can be requested on a watch **with no stored bucket of its current version** (a new watch;
a watch after a version bump; a disabled watch whose data were deleted) through the create or patch
body (`history_days`) or `POST /property-watches/{id}/history` (§H.9.1). For a watch with stored
buckets the route answers 409 with the count and the reason, and the operator creates a new watch
(the lab does this). Prepending is Q3.

### H.5.6 Statements per tick stay constant (rule 9)

A history step adds no per-watch, per-value or per-bucket statement. Per tick, beyond the live
count: one `pg_try_advisory_xact_lock` per `READING` watch (a constant per watch, not per value or
bucket), and the same bulk reads and one-statement-per-table writes (`values`, `buckets`, `metrics`,
`watches`; `pw_collector.py:723-735`). The history columns ride on the existing `update_watches`
`executemany` (`repositories/property_watch.py:420-438`) as extra bind parameters. A pin test, in
the shape of `tests/integration/test_pw_engine_scale.py`, asserts the history tick's statement count
is equal for 1 and 10 `READING` watches, for 6 and 200 values and for a 12-bucket and a
2,016-bucket chunk.

---

## H.6 Fail closed, in one table

Every row is `INSUFFICIENT_DATA`, a gap, or a named `history_state` with a reason. None is a zero,
none is `NORMAL`, none lets a detector grant itself lower-bound reasoning (AGENTS.md rule 8).

| # | Condition | Result |
|---|---|---|
| 1 | **Retention gap.** Buckets before the first event of the scope plus the margin | not written, no row, not a zero; `history_floor` states where history starts; `PARTIAL` if the asked window was cut, `UNAVAILABLE` if the scope has no event in it (§H.3.2) |
| 2 | **A property that did not parse in old events.** A history bucket whose coverage (`parsed / total`) is below `pw_min_coverage` (0.90), or `parsed_events = 0` with events (`PwBucketFact.coverage`, `facts.py:295-308`) | the bucket **is stored**, as measured (it is real data) and marked by its coverage; it is **excluded from training** as `low_coverage` (`property_watch/baseline.py:92-113`, existing), and **fails `verify_value_history`** for novelty. `verify_value_history` takes a new `min_coverage` argument applied to `origin = 'HISTORY'` buckets: "history bucket X had parsing coverage below the floor". The new-value window can therefore not complete over such buckets: that is the incomplete history D-27 asks for. The live rows are not changed (Q5 asks whether they should be) |
| 3 | **A truncated or failed chunk.** A GROUPED sub-window cut at the row cap; a TOTALS or PINNED search that is not whole; a failed search; a timeout | cut: split in time (not kept from the multi-bucket search), and at one bucket stored `LOWER_BOUND`, which `verify_value_history` rejects (`history.py:61-64`). Failed: no row, `J` holds, `W` halves, at the third failure at 12 buckets the sub-window is a recorded gap (§H.4.2). A multi-bucket truncated result is never written (AGENTS.md rule 8, `keyed_search.py:11-12`) |
| 4 | **Today's scope membership applied to the past.** A group is resolved from the inventory now (`logsourceid IN (<ids>)`, §G.4); sources that were members then and are not now are absent from the past counts, and members that joined later contribute events from before they joined | stated limitation. History buckets carry today's `scope_member_count` and fingerprint with `membership: current` in `query_provenance`; the page says it (§H.9.3). A type scope (`devicetype = n`) is historical by construction; a source scope is one id. A group whose membership changes *during* the reading is flagged by `scope_changed_flags` as in live |
| 5 | **The admission caps hit during the bootstrap.** `pw_max_new_values_per_bucket`, `pw_max_active_values_per_watch`, more than `pw_max_values_per_bucket` distinct untracked values in a bucket | the bucket records `rejected_over_quota` and `admission_truncated` exactly as live (`admission.py:176-213`), so `verify_value_history` rejects it and a first appearance in the 7 days after it is `INSUFFICIENT_DATA`. The caps never silently drop a value that later reads as new |
| 6 | **A source that did not exist yet.** A source scope or a group member not yet sending | its leading silence is the leading silence of row 1: nothing before the first event plus the margin is written |
| 7 | The scope is `SCOPE_STALE`, `SCOPE_EMPTY` or `SCOPE_TOO_LARGE` | no step, no bucket; the watch holds (`pw_collector.py:465-471`) |
| 8 | `parsed_events = 0` with `total_events > 0` for a whole cadence of history | stored as measured; no `NOT_APPLICABLE` status from history (it judges nothing); the live check resumes at hand-off |
| 9 | The reading did not finish in `pw_bootstrap_max_hours`, or the switch went off, or the search budget starved it | hand-off `PARTIAL` with the reason, no row for the unread span (an implicit gap), live starts at the boundary |
| 10 | A bucket whose search left the span unmeasured (budget spent mid-step) | left for the next step; `J` moves only past the contiguous completed prefix |
| 11 | `pw_bootstrap_enabled` false | no history is read; a watch asking for it starts live as today, `history_state = PARTIAL` with reason "history reading is switched off" |
| 12 | The density gate (§H.4.3): more than 5 % of buckets `LOWER_BOUND` | the bootstrap ends `PARTIAL` with the number; further reading would only store lower bounds |
| 13 | `history_days` below `pw_new_value_history_days`, or above `pw_bootstrap_days` | 422 at the API; the settings validator refuses the combination at startup |

---

## H.7 Values

### H.7.1 Admission during the bootstrap

The same pure `admit_bucket` (`admission.py:143-215`) runs oldest bucket first, so a value admitted in
one bucket is tracked in the next, which is what its docstring requires (`:150-156`). The policy is
the live one (`pw_min_admission_events` 3, `pw_max_new_values_per_bucket` 20,
`pw_max_active_values_per_watch` 200, `pw_value_active_days` 7: `config.py:644-652`). Nothing is
relaxed for history.

**Which values win when the cap binds.** Within a bucket, `candidates` arrive ordered by events, then
key (the fold's order), and `eligible[:quota]` are admitted (`admission.py:176-181`): the most
frequent in that bucket win. Across buckets the winner is the **earliest to reach the ceiling**: once
200 values are active no new value is admitted until one retires. Over a 28-day window this favours
values that appeared early over equally frequent ones that appeared later. It is the order live
collection would have produced had the watch run for 28 days, and it is honest because it is
visible: every bucket where it bit has `rejected_over_quota > 0`, which blocks novelty claims in the
following 7 days (§H.6 row 5), and the watch page says "N values were refused admission" with the
ceiling. The remedy is `pw_max_active_values_per_watch`, not a smarter history.

### H.7.2 `first_seen_at` and `tracked_since` from history

A value admitted at history bucket `b` gets `first_seen_at = tracked_since = b`, a time in the past,
while its `created_at` is the time of the reading (`admission.py:78-92`). That is the meaning the
detectors need: `first_seen_at` is the first bucket in which the value reached the admission
threshold **at or after `F`**. The page never says "first seen ever"; it says "first seen in the
history read from QRadar (from F)". A pinned value is `tracked_since` the first bucket of its first
step (`sync_pins`, `pw_collector.py:643-644`), i.e. `F`, so its measured zeros from `F` on are
provable (the PINNED search covers the span).

### H.7.3 Retirement while reading old values

`retire_stale` retires an active, unpinned value unobserved for `pw_value_active_days` and sets
`tracked_until` to the reference bucket (`admission.py:218-245`), so that a lagging read cannot treat
absence as zero. The live path calls it once per tick with the tick's last bucket. A history chunk may
span days, so the call at the end of a chunk would (a) leave a long-gone value counting against the
ceiling for the rest of the chunk, which makes the ceiling bind early, and (b) set `tracked_until`
days late. The history path therefore calls `retire_stale` after **every bucket** (pure, in memory, no
statement), so the active count and the tracking windows are what bucket-by-bucket collection would
have produced. A value retired during history stays a row, `active = false`, with its metrics, until
retention deletes it (`pw_retention.py`). Rows created over a 28-day window are bounded: at most the
ceiling's worth are active at once and a value stays active at least 7 days after it was last seen,
so the churn is roughly the ceiling per 7 days (about 1,000 rows over 28 days at the defaults), not
the 20-per-bucket quota times 8,064.

A value a history bucket retired is never `OPEN`/`RECOVERING` (nothing was ever judged), so the
resolve-on-retire path (PW-3c item 1) has nothing to do.

### H.7.4 Pins

Pins behave as in §G.7.3: always tracked, never retired, counted toward the ceiling. The PINNED
search runs over every span, so a pinned value's measured zero is provable per bucket in history as
in live. A pin added or removed during the reading takes effect at the next step's first bucket
(§H.5.4). A pin added after hand-off is live-only (§G.7.3 is unchanged).

---

## H.8 Training

### H.8.1 8,064 buckets without breaking rule 9

Training is untouched. `PwBaselineTrainer.rebuild_due` takes the `pw_baseline_values_per_run` (100)
least recently computed values and rebuilds them in one chunk of a fixed number of statements: the
due read, then `buckets_between`, `metrics_between`, the incident-span read, the baseline upsert and
the stale-cell delete (`property_watch/training.py:67-157`; 6 statements per chunk after PW-3c,
`docs/tasks/PW-3c.report.md`, "Item 3"). The number of statements is constant in values, buckets and
watches; what grows with the bootstrap is the **rows** one statement returns: up to
100 values x 8,064 buckets = 806,000 metric rows (`docs/tasks/PW-3b.report.md`, "Open points"; the
trainer loads ORM objects, `repositories/property_watch.py:248-262`). That is the steady state at day
28, reached at day 1. It breaks no statement bound; it is a memory and time question, and the build
task changes `metrics_between` for the trainer to select the three columns it reads
(`pw_value_id`, `bucket_start`, `events`) as tuples instead of entities (PW-13c, §H.13), which
removes most of the object overhead. A 200-value watch is trained in two runs (two ticks) after
hand-off; the values not yet trained read `INSUFFICIENT_DATA` ("learning") until their turn, never a
verdict.

The trainer reads buckets up to `evaluated_through` (`training.py:71`, `repositories/property_watch.py:540`),
which is the last history bucket at hand-off, so the first training pass uses the whole window.

### H.8.2 When the seasonal cells become reliable

A cell is reliable when it has at least `baseline_min_samples` = 8 clean samples (`config.py:335`).
A visit to an hour-of-week supplies up to 12 five-minute samples. Counting a visit's samples as
available only where the value was tracked and the reading was exact, covered and not excluded
(`baseline.py:92-113`):

| Window read | Visits per cell | Samples per cell (max) | Reliable at hand-off |
|---|---|---|---|
| 7 days | 1 | 12 | yes, for every hour-of-week where the value was tracked and at least 8 of its 12 buckets were clean |
| 14 days | 2 | 24 | yes |
| 28 days | 4 | 48 | yes, with the spread of four weeks |

So **a cell is judged from hand-off after a bootstrap of 7 days or more**, where §G.8.0 says one
week after first tracking. The weaker claim at 7 days is statistical, not a gate: the 12 samples of
one visit are correlated and understate the week-to-week spread, which raises the chance of a false
spike or drop for the first weeks. That is why the default window is 28 days and why the lab may
use 7 for speed. A value admitted late in the window has fewer visits; `observations` and
`sample_count` are stored per cell and the page's "baseline: N of 168 cells reliable" reads them
(`baseline.py`, §G.8.0).

---

## H.9 API and UI

### H.9.1 API

All additive; nothing removed (`backend/app/api/routes/property_watches.py`,
`backend/app/schemas/property_watch.py`).

| Change | Detail |
|---|---|
| `POST`/`PATCH` body | `history_days: int \| null`. Validated against `pw_new_value_history_days` and `pw_bootstrap_days` (422 with every reason, the F.1 pattern, §G.11). Changing it on a watch with stored buckets of its current version is 409 |
| `POST /property-watches/{id}/history` | `property_watch:write`. Body `{history_days}`; allowed only for a watch with no stored bucket of its current version (§H.5.5); audited as `property_watch.history_request` with the count, never a value |
| `PropertyWatchOut.history` | `{state, days_requested, days_target, floor, through, end, days_read, searches, buckets_read, gaps, reason, started_at, finished_at}`; `days_read` and `days_target` computed from `history_through`, `history_floor` and the boundary, never invented. **No ETA** is returned: the cost table (§H.4.5) is a design figure, not a promise |
| enable response (`PATCH {enabled: true}`) | the same `history` object, plus `history.estimate = {searches_min, searches_max, ticks_min, ticks_max, cadence_seconds}` from the static model (`rho` from the pins at the low end, from the ceiling plus the fold's cut at the high end). The 409 for a full tick budget names the free and the needed numbers |
| `status` | the new `READING_HISTORY` is a watch status with a reason ("Reading history from QRadar: 9 of 28 days; nothing is judged until it finishes") |
| `GET /property-watches/catalog` | gains `history: {enabled, days_default, days_max, searches_per_tick, seconds_per_tick, reading_count, searches_free}`, beside the existing `search_budget` and `budget_used` (`services/property_watches.py:417-492`) |
| `GET .../buckets`, `.../series`, `.../values` | each bucket and point carries `origin`; `first_seen_at` of a value stays the history time |
| `GET /anomalies/{id}` evidence | adds `history_buckets_from_history: n` next to `history_buckets_verified`, and `history_floor` |
| `/aql` | lists the history searches (the generated text of the last history step is in the bucket's `query_provenance`, as for live) |

### H.9.2 The states, by guidelines §4 and §6

| State | Where it shows | Wording (no "live", no "real-time", no exclamation mark) |
|---|---|---|
| `READING` | the status pill on the list and the page header; the widget (`empty`) | "Reading history from QRadar: 9 of 28 days. Nothing is judged until it finishes." |
| `COMPLETE` | one line in the honesty block | "History: 28 days read from QRadar on 2026-10-19, used for baselines and new-value checks. Those buckets were not judged." |
| `PARTIAL` (Ariel holds less) | `EvidenceBanner`, state `PARTIAL` | "History: 9 of 28 days. QRadar holds events of this scope from 2026-10-10 09:00 UTC; earlier buckets are not claimed as zero." |
| `PARTIAL` (a gap, a limit) | same | the reason: "buckets from 2026-10-12 14:00 to 2026-10-12 16:00 UTC could not be read after 3 attempts", "the reading did not finish in 72 hours" |
| `UNAVAILABLE` | same, as its own section (guidelines §6, never an empty success) | "QRadar holds no events of this scope in the last 28 days. The watch learns forward." |
| `LOWER_BOUND` history buckets | the chart, the honesty line | the existing "at least n" and "12 of 288 buckets are lower bounds" rules, now counted over history too |
| stale | not applicable while `READING` (no live bucket is expected) | |
| forbidden | unchanged (`read:property_watches`) | |

A history bucket is **never** hidden: the chart draws it, labelled in text "read from QRadar
history, not judged", with the existing incident-span shading mechanism in a neutral token (no new
colour, no gradient), and the same table of text beneath for assistive technology (§10).

### H.9.3 Pages

- **Editor** (`/behavior/watches/new`, `WatchEditor.tsx`): a sixth labelled section, **History**, a
  checkbox "Read history from QRadar (used only for learning)" and a choice of 7, 14 or 28 days up to
  `days_max`; the deterministic summary sentence gains "reads 28 days of history first"; beside it the
  estimate "about 7 to 22 searches, 1 to 2 runs"; and the three limits in plain words: "events whose
  property was not parsed then are not counted as history", "the membership of a group is today's, not
  as it was", "nothing is judged until the reading finishes". Disabled, with the reason, when
  `pw_bootstrap_enabled` is false ("history reading is switched off on this server") or the tick
  budget has fewer than 3 free searches.
- **Watch page**: the header line shows the history state; the honesty block (§G.13.3 item 5) gains the
  states of §H.9.2; the values table's `First seen (UTC)` is captioned "from the history read from
  QRadar, from <F>"; the **Definition** tab shows `history_*`, the gaps, the floor and "membership is
  today's".
- **Investigation view** (`/anomalies/[id]`, PW detail): for `PW_NEW_VALUE` and `PW_NEW_COMBINATION`
  the evidence states "the 7 days before 2026-10-20 09:05 UTC were verified: 2,016 buckets, of which
  2,016 were read from QRadar history on 2026-10-19 and not judged", so an analyst sees where the
  absence claim comes from, and the wording stays "not observed [...] in the 7 days before", never
  "never" (§G.8.3).
- **Widget** `chart.property_watch` (`frontend/src/widgets/pwWidgetState.ts:33-38`): a `READING`
  watch with no live bucket is the `empty` state, with the reason and "reading history: n of m days"
  and the drill-down; after hand-off the first live bucket makes it `ok`. `READING_HISTORY` is added
  to `pwFormat.ts` (label, reason, tone `info`/`warn` as for `STALE`) and `lib/api.ts`. The widget
  shows no history chart.
- **List** (`/behavior/watches`): a `READING` watch sorts as needing attention only if it has been
  reading longer than `pw_bootstrap_max_hours`; otherwise it shows the pill and the progress.

Vitest covers each state of §H.9.2, the editor's disabled reasons and the deterministic sentence;
screenshots at 1440x900, 1280x800 and 1024x768 of the list, the editor, a `READING` page, a
`PARTIAL` page, an `UNAVAILABLE` page, a `COMPLETE` page, the investigation view, and a dashboard
with the widget in `READING` (AGENTS.md §3a; `docs/UI-WORKSTREAM-COMPLETION.md` §4).

---

## H.10 Settings

All in `backend/app/core/config.py`, validated only where `pw_collection_enabled` is on, like the
other PW cross-checks (`config.py:906-945`), and additionally where `pw_bootstrap_enabled` is on.

| Setting | Default | Bounds and notes |
|---|---|---|
| `pw_bootstrap_enabled` | **true** (D-29) | the kill switch; the text below argues for false, the user chose true |
| `pw_bootstrap_days` | 28 | `>= pw_new_value_history_days`, `<= pw_baseline_lookback_days` |
| `pw_bootstrap_searches_per_tick` | 12 | `1..256`, `<= pw_max_searches_per_tick`; the history sub-budget of §H.4.4 |
| `pw_bootstrap_seconds_per_tick` | 180 | `10..3600`; no history search starts after it |
| `pw_bootstrap_floor_margin_hours` | 24 | `1..168`; §H.3.2 |
| `pw_bootstrap_max_hours` | 72 | `1..168`, and `pw_bootstrap_days x 24 + this < pw_metric_retention_days x 24` |
| `pw_bootstrap_max_failures` | 3 | `1..20`; consecutive failures at 12 buckets before a gap |
| `pw_bootstrap_max_lower_bound_share` | 0.05 | `0..1`; the density gate |

The sub-window width, the 80 % row target and the 12-bucket floor are constants of the planner, not
settings (they follow `pw_max_rows_per_search`).

**The kill switch defaults to off, and the lab turns it on.** Reasons, in order of weight:

1. Three facts about the production console are unknown and cannot be read from the repository: how
   its retention buckets are defined (**[C-2]**), whether old events keep their payload for custom
   properties (**[C-3]**), and how long wide grouped searches take (**[C-5]**). The design is
   fail-closed for the last two but rests on A1 for the first.
2. A bootstrap spends Ariel searches the operator did not ask for at that moment (§H.4.5: up to
   about 400 for a heavy watch); a new default-on load belongs behind an explicit decision, as
   `pw_collection_enabled` and `pw_alerting_enabled` are (`config.py:628-630`).
3. Off is a safe state: a watch that asks for history while it is off starts live as today and says
   so (§H.6 row 11).

Q1 asks the user to flip the default once the probes have been read.

---

## H.11 Migration

One additive migration, number **`0029_pw_history_bootstrap`** (orchestrator, 2026-10-18: T-134 takes `0028` for the D-28 cadence default, so this one follows it; the head was `0027_custom_rule_pw_value`,
`backend/alembic/versions/`). It:

- adds the `property_watch` columns of §H.5.1; `history_state` is added `NOT NULL` with a temporary
  server default `'NOT_REQUESTED'` and the default is then dropped, and `history_progress` the same with
  `'{}'`, so no persistent server default is left (the repository invariant, `0010`'s test
  `test_0010_backfills_existing_rows_and_leaves_no_server_default`,
  `backend/tests/integration/test_migrations.py:543`; PW-1's note, §G.3);
- adds `pw_bucket.origin` `NOT NULL` with a temporary server default `'LIVE'`, then drops the default.
  `pw_bucket` is a hypertable (`0026_property_watch.py:254-280`): the test adds the column on a
  hypertable that has data **and** one with a compressed chunk, since a `NOT NULL` column with a default
  on a compressed hypertable depends on the TimescaleDB version in use (**open point**, tested in
  PW-13a);
- adds `CHECK`s `history_state_known`, `origin_known`, `history_days_range`;
- drops and recreates `last_status_known` to include `READING_HISTORY` (the `0027` pattern for a
  recreated `CHECK`, `docs/PHASE-G-PROPERTY-WATCH.md` §G.7.4); the downgrade first sets any
  `READING_HISTORY` status to NULL, then restores the old `CHECK`;
- downgrade drops the added columns and the `CHECK`s; history rows (`origin = 'HISTORY'`) are deleted
  first, with their metrics (they are not reproducible without the columns), and `history_state` of a
  `READING` watch is lost, which restarts it as a live-only watch.

It must upgrade, downgrade and upgrade again with data carried, and `alembic check` must report no
drift (AGENTS.md rule 7). Tests: a round trip with a `LIVE` bucket, a watch and a status of
`READING_HISTORY` present before the downgrade; the recreated `CHECK` accepts the new status and
refuses an unknown one; `pw_bucket.origin` of old rows is `'LIVE'`; no server default remains on the
new columns; the compressed-chunk case. It is the only migration of this build; no other PW-13 task
adds one.

---

## H.12 The lab demonstration: a new value judged against bootstrapped history, in about an hour

### H.12.1 What it must show, and what decides if it can

Show, in one run of about an hour: (a) a new watch with history reads its past, and no bucket of it
is judged; (b) at hand-off the live buckets continue with no gap and no overlap; (c) an *established*
value that reappears is `HEALTHY`; (d) a **new** value is `ANOMALOUS` and opens an incident; (e) a
bucket with unusable history is `INSUFFICIENT_DATA` with its reason.

It can only show (d) if the scope has at least `pw_new_value_history_days + margin/24` = **8 days** of
events in Ariel, because `F = b0 + 24 h` and the learning check needs `first_seen >= F + 7 d`
(`detectors.py:600-612`). **Whether source 212 holds 8 days is not known [C-1].** P-1 decides, and the
run is planned in two cases:

| Case | Condition (from P-1/P-2) | Settings of the run | Expected for the new user |
|---|---|---|---|
| A | scope has events from `b0 <= now - 8 d` | defaults; `PW_BOOTSTRAP_DAYS=7` for speed | `ANOMALOUS` (below) |
| B | `b0` is 4 to 7 days ago | `PW_NEW_VALUE_HISTORY_DAYS=3`, `PW_BOOTSTRAP_DAYS=3` (history length is a timing setting, not a detection threshold, the LAB_MODE rule `config.py:45-52`) | `ANOMALOUS` after 3 days of history |
| C | `b0` is under 4 days ago | defaults, `PW_BOOTSTRAP_DAYS=7` | `INSUFFICIENT_DATA`, reason "learning: day n of 7": the fail-closed demonstration, no detection |

### H.12.2 Generator plan (lab, rate limit 10 events/s, as PW-10)

PW-10's profile: `ips` LEEF, hostname `ips-gw-01`, six synthetic users on source 212, normalized
property `username`, pinned `compromised.user` (PW-10 report section 1). The clock below is relative;
the lab task computes absolute UTC from `date` before starting.

| t (min) | Action |
|---|---|
| -10 | run P-1/P-2 (a handful of searches); choose case A/B/C; create the watch disabled with `history_days`, run the preview (2 searches) |
| 0 | set the PROPERTY_WATCH cadence to 300 s (`SearchCadenceService.set_cadence`, as PW-10); enable the watch; start S1, steady mix of the six users, 1.2 events/s, until t = 60 |
| 0 to 10 | tick 1: the scan and the first span (up to 12 searches); tick 2: finish and hand off (6 values, 7 days: 7 searches, §H.4.5). `READING_HISTORY` until then |
| 10 | hand-off; the training tick; record `history_end` |
| 10 to 25 | live buckets; the six users `HEALTHY` or `INSUFFICIENT_DATA` per their cells |
| 25 | start S2, `--fixed-username newuser.demo`, 5 events/s for 15 minutes (about 1,500 events per bucket: above admission, above materiality) |
| 25 to 60 | `newuser.demo` first appears; `PW_NEW_VALUE` goes `ANOMALOUS` at its second bucket and the incident opens at the third (`anomaly_open_after_intervals` 2; materiality needs 2 buckets, `detectors.py` `_materiality`); S2 ends at 40; recovery counts follow |
| 60 | stop; read-only snapshots; disable and delete the watch, detach nothing (no F.1 rule in this run) |

### H.12.3 Search budget

About 3 searches per live tick (grouped, totals, pinned) x 10 ticks = 30; the history step 7 to 12;
the preview 2; P-1/P-2 up to 15 (the probe task of §H.3.5 is run before, with its own count). Total
about 60, against the 80-search limit PW-10 used. Statement counts and Ariel times are recorded per
tick as PW-10 does (`ticks.jsonl`).

### H.12.4 Expected verdicts and checks

| Check | Expected |
|---|---|
| history buckets in `pw_bucket` | `origin = 'HISTORY'`, contiguous 5-minute starts from `F` to `history_end - 5 min`, no duplicate |
| first live bucket | starts at `history_end`, `origin = 'LIVE'` |
| rows judged in history | **none**: no `pw_anomaly`, `pw_anomaly_transition` or alert with an interval before `history_end` (SQL, asserted) |
| the six users after training | `NORMAL`/`HEALTHY` with reliable cells (`D >= 7`); the pinned value exact |
| optional second run: an established user stops after hand-off (S1 with five users from t = 25) | `PW_VALUE_DISAPPEARED` opens at the 7th live bucket, not before (the live-only streak of §H.1.3) |
| `newuser.demo` | `PW_NEW_VALUE` `ANOMALOUS` with the history window verified, "N of N history buckets read from QRadar history" in the evidence |
| a deliberately flooded bucket (a second run with `PW_MAX_NEW_VALUES_PER_BUCKET=1`) | the bucket has `rejected_over_quota > 0`; a first appearance in the 7 days after it is `INSUFFICIENT_DATA` |
| a worker restart between the two history ticks (`PW_BOOTSTRAP_SEARCHES_PER_TICK=2` makes the reading last four ticks) | resumes at `history_through`; the final rows are the same as an uninterrupted run |
| a tick run by hand between two scheduled ones | no gap, no overlap, no second advisory-lock holder |

The lab run also measures what the cost table assumed: seconds per history search by window, rows
per sub-window, the 806,000-row training read (§H.8.1), and the statement count of a history tick.

---

## H.13 The build, split into tasks

Named `PW-13…` so they cannot be confused with the design (`PW-12`). Agent kinds as the PLANNER uses
them: Codex for migrations and SQL-heavy cores, Sonnet for the rest. Sizes: S under half a day, M
about a day, L two days. All build tasks wait for the user's review of this file (D-27 "built after
review") and for PW-10 to finish.

| ID | Task | Agent | Size | Files | Depends on | Tests |
|---|---|---|---|---|---|---|
| **PW-13p** | **Lab probe** (read-only, §H.3.5): P-1 to P-8, at most 40 searches and 6 GETs; a throwaway script outside the repository; a report with the raw results and the verdict on A1, A2, A3 and the cost model | Sonnet | S | none in the repository; `docs/tasks/PW-13p.report.md` | PW-10 finished | none (a probe); the report states each [C-n] answered or not |
| **PW-13a** | Foundations: migration `0029`, models (`PropertyWatch` history columns, `PwBucket.origin`, `PwWatchStatus.READING_HISTORY`, `PwHistoryState`, `PwBucketOrigin`), the eight `pw_bootstrap_*` settings with validators, schemas | Codex | M | `backend/alembic/versions/0029_pw_history_bootstrap.py`, `app/models/property_watch.py`, `app/models/enums.py`, `app/core/config.py`, `app/schemas/property_watch.py` | review of this file | migration round trip with data, hypertable and compressed chunk, `alembic check`, `CHECK` accept/refuse, no server default left, settings validators (each bound and each cross-check), enum/CHECK parity test |
| **PW-13b** | The pure planner: floor scan interpretation (`scan_floor`), step planning (`plan_step`: span, sub-windows, density adaptation, `W` halving, gaps, density gate, completion), the history state machine | Sonnet | M | new `app/property_watch/bootstrap.py` and its unit tests | PW-13a (enums) | property tests: contiguity (the chunks tile `[F, e)` exactly), no overlap, a failed or truncated search never advances `J`, a zero before `F` is never produced, `W` stays in `[12, 2016]`, the cost model of §H.4.5 reproduced by the planner on scripted densities, the state machine's every exit |
| **PW-13c1** | Collector integration: the history branch of `PwCollector` (live first, then history under the sub-budget and time budget, the advisory lock), per-bucket `retire_stale`, `origin` on `_bucket_row`, the history columns on `update_watches`, `READING_HISTORY` status, hand-off | Codex | L | `app/collectors/pw_collector.py`, `app/repositories/property_watch.py`, `app/services/pw_pipeline.py` | PW-13a, PW-13b | the equivalence test (§H.2.5), the hand-off table of §H.2.4 (zero gap, zero overlap, a half-done tick, two overlapping ticks, a crash mid-step), the statement-count pin (§H.5.6), the budget order (live first, starved history), every row of §H.6 that the collector decides (1, 3, 7, 9, 10, 11, 12) through `MockQRadarProvider.pw_script`/`pw_fault`, the flag-off path, the version-bump restart |
| **PW-13c2** | Learning-only guards and detector inputs: `pending_buckets` origin filter, `_recent` LIVE-only, `_established` for `first_seen_at < history_end`, `verify_value_history` `min_coverage` for HISTORY buckets, F.1 `load_pw_values` origin filter with `include_history`, the trainer's column select, the reader classification guard test | Codex | M | `app/repositories/property_watch.py`, `app/property_watch/engine.py`, `app/property_watch/history.py`, `app/property_watch/facts.py` (`origin` on `PwBucketFact`), `app/property_watch/training.py`, `app/custom_rules/loaders.py`, `app/custom_rules/preview.py` | PW-13a | `test_history_buckets_are_never_pending` (three guards, each broken on purpose), the reader classification test, a HISTORY bucket never reaches an F.1 breach (including the kind-watermark race of §H.1.2), disappearance cannot open before 7 live buckets, a value first seen in history is established, the coverage row (§H.6 row 2), training of 100 values x 8,064 buckets stays at 6 statements |
| **PW-13d** | API and service: `history_days` on create and patch, `POST .../history`, the 409s (stored buckets, budget), `history` on the watch, the catalog fields, bucket and series `origin`, the evidence fields, the version-bump restart in the patch route, audit | Sonnet | M | `app/api/routes/property_watches.py`, `app/services/property_watches.py`, `app/schemas/property_watch.py` | PW-13a, PW-13c1 | permission matrix (401/403/404/409/422), the estimate, the bump restart, enable with a full budget, no AQL accepted anywhere, flat statement counts on the new reads |
| **PW-13e** | UI: editor History section, watch page states, honesty line, investigation wording, list pill, widget `empty` mapping, the `authoring.ts` and `pwFormat.ts` strings | Sonnet | L | `frontend/src/components/propertywatch/*`, `frontend/src/app/behavior/watches/**`, `frontend/src/widgets/pw*`, `frontend/src/lib/api.ts` | PW-13d | Vitest for every state of §H.9.2, the colour-literal test, screenshots at the three viewports (paths in the report) against a deterministic fixture API |
| **PW-13f** | Live validation: the demonstration of §H.12, the checks of §H.12.4, the measurements of §H.12.4 last paragraph | Sonnet | M | none in the repository; a report | PW-13c1, PW-13c2, PW-13d, PW-13e, PW-13p | the table of §H.12.4 |
| **PW-13g** | Docs: README routes and settings, `OPERATIONS.md` (the switch, the signs of a stuck reading), the sentences of §H.0.4 in `PHASE-G-PROPERTY-WATCH.md`, `SEARCH-CADENCE.md` §SC.5, the `[BUILT]` markers | opencode free model | S | the files named | all above | none |

Order: PW-13p in parallel with PW-13a, then PW-13b, then PW-13c1 and PW-13c2 in parallel with PW-13d,
then PW-13e, then PW-13f, then PW-13g. The longest chain is PW-13a, PW-13b, PW-13c1, PW-13d, PW-13e,
PW-13f.

---

## H.14 Open questions, console facts and disagreements

### H.14.1 Open questions for the user

**Q1. Should `pw_bootstrap_enabled` default to on?** Recommendation: **off in the code, on in the lab
profile, and flip the default after PW-13p's report has been read** (§H.10). The cost of off: a
production operator must set one environment variable. The cost of on today: an unrequested search
load built on three unproven console facts.

**Q2. The default window: 28 days or 7?** Recommendation: **28** for the setting and the editor's
default (four visits per cell, §H.8.2), with 7 one click away and used by the lab for speed. The
cost: a 200-value watch takes about three hours of 5-minute ticks instead of 45 minutes (§H.4.5).

**Q3. Existing watches: no bootstrap at upgrade, and none by request once buckets exist?**
Recommendation: **yes, no prepending in this build** (§H.5.5); create a new watch to get history.
Prepending needs the value-ordering conflict resolved (§H.0.3) and is a separate design if the user
wants it. The cost: watches already running learn forward as they do now.

**Q4. The blind period.** The moving end (§H.2.4) makes the minutes to hours of reading history, never
judged. Recommendation: **accept it**; the alternative replays up to hours of live buckets late into
alerts. The page states it. For a small watch it is minutes.

**Q5. Should the coverage floor also apply to live buckets in `verify_value_history`?** Today it
reads no coverage (§H.14.3, item 1). Recommendation: **history only in this build**; a live parsing
break should be answered by the existing `NOT_APPLICABLE`, and applying the floor to every bucket
would stop novelty for scopes that parse at 60 % steadily. Decide separately if wanted.

**Q6. May a history step run faster than one per cadence slot?** Recommendation: **no**; it would
add Ariel load outside the operator's one schedule control (D-7). With D-28 at 5 minutes the reading
of a lab-sized watch finishes in minutes (§H.4.5).

**Q7. The margin: 24 hours?** Recommendation: **24 h** until P-7 measures the ramp at a real floor;
the cost is one day of 28.

**Q8. The retention assumption A1.** The page will say "assumes one retention policy for this
scope". Recommendation: **accept it for the lab**, and for production confirm with the console owner
what retention buckets exist (P-3 shows whether the API lists them) before the default is flipped
(Q1).

### H.14.2 Console facts not established, each with its probe

| # | Fact | Probe | If it turns out otherwise |
|---|---|---|---|
| C-1 | How far back the lab's scopes hold events (source 212, type 368, the others) | P-1, P-2 | decides which demonstration case (§H.12.1) runs |
| C-2 | Retention buckets and their filters; whether deletion is oldest-first per scope (A1) | P-3 (`/api/help/endpoints`), P-1, P-2 | if scope events are split in short and long classes, the run-time proof of §H.3.2 is not enough; the setting stays off for that console and the design needs a per-class proof (a redesign of §H.3) |
| C-3 | Whether old events keep the payload a custom property is extracted from, and whether an optimised property is extracted at ingest only | P-4 | the coverage rule (§H.6 row 2) already fails closed; if old events never parse, history for custom properties is `PARTIAL` or useless and the editor should say so |
| C-4 | The ramp of counts near the retention floor (a partial first day) | P-7 | sets the margin |
| C-5 | Ariel time against window width and rows, for a custom property and for `username`; whether a 168 h grouped search stays below 120 s | P-5, P-6, P-8 | the sub-windows shrink and the ticks of §H.4.5 grow; if 168 h times out, spans are capped lower |
| C-6 | Whether the `START`/`STOP` pad (`ariel_stop_pad_seconds`, `config.py:136`, `qradar_rest.py:292-312`) behaves on windows days old | covered by P-5 (compare an old window's counts with and without the pad) | the pad is capped at "now" and floored at the window end, so on old windows it only widens the scan by 60 s; a count difference would mean the pad leaks events |

### H.14.3 Where the code and the Phase G text disagree (the code wins)

1. **`verify_value_history` reads no coverage.** §G.5 says "any verdict that reads an absence as
   evidence [...] requires coverage >= `pw_min_coverage`" (the list names disappearance, drop,
   "below", `NORMAL`); the new-value claim is an absence claim, and `history.py:12-82` takes no
   coverage input. A live parsing break followed by recovery lets a returning value read as new.
   Pre-existing; the bootstrap makes it matter (payload expiry), so §H.6 row 2 adds the check for
   history buckets only (Q5).
2. **`watch_first_collected_at` is the minimum stored bucket, including a `NOT_COLLECTED` row.**
   §G.8.3 says "from the watch's first collected bucket"; `repositories/property_watch.py:178-186`
   takes `min(bucket_start)` over every stored row of the version, a failed first bucket included.
   Harmless today; with history the minimum is the oldest history bucket, which is the intended
   meaning.
3. **Re-enable does not start fresh for the watermarks.** §G.10.1 says "Enabling again starts fresh"
   (about incidents); `PATCH` neither resets `collected_through` nor `evaluated_through`
   (`routes/property_watches.py:731-863`), so a watch disabled for days catches up
   `cadence/interval + collection_max_backfill_intervals` buckets per tick from the old watermark
   (`pw_collector.py:348-357`) and evaluates them. Not in this task's scope; it is a replay of old
   events into evaluation of the kind D-27 forbids for history. Reported, not changed.
4. **A version bump keeps the watermarks.** §G.3 says the new version has no baselines and no
   history, and the engine reads only the current version; but `collected_through` is not reset
   (`routes:826-828`), so the first bucket of the new version is the one after the old watermark.
   For a bootstrap restart (§H.5.4) the watermarks must be reset; the patch route does that only for a
   watch with `history_days`.
5. **The search window bound.** §G.6/§G.14 speak of windows of "whole buckets" with no bound; the code
   refuses a window above 168 h (`bucketed_metrics.py:28`). The history span is bounded by it.
6. **The search timeout.** §G.14.1 gives measured times of 0.4 to 3.5 s; `_run_bounded_aggregate`
   gives a search 120 s (`qradar_rest.py:2114`) and uses a fixed 1 s sleep; the setting
   `ariel_poll_interval_seconds` (`config.py:127`) is not used there.
7. **`admission_truncated` is set by the fold's cut as well as by quota.** §G.7.3 describes it as the
   outcome of a binding ceiling; the code sets it when `rejected > 0` **or** when the fold kept fewer
   values than it observed (`admission.py:204-213`), including values below the threshold. A bucket
   with more than `pw_max_values_per_bucket` distinct untracked values is therefore never valid
   history. Conservative (fail closed), but it limits what a bootstrap can give for a
   high-cardinality property (§H.4.3).
8. **`pw_bucket`'s primary key has no `watch_version`.** §G.7.1 stamps buckets with the version "and
   never compares across it"; the key is `(watch_id, bucket_start)` (`models/property_watch.py:210-212`),
   so a rewritten window overwrites the other version's rows (§H.5.4). Harmless to readers.
9. **The PW dispatch has no advisory lock.** The other families hold one per instance or source
   (`workers/tasks.py:745,891`); `dispatch_property_watch_collection` (`:1394-1442`) relies on
   `claim_slot` alone. A history step is long enough to need the lock (§H.2.4).
10. **The cadence default.** §G.3 and the 0026 seed say 1 h; D-28 says 5 minutes (T-134 pending).
11. **The static enable cost counts steady state only** (`search_cost`,
    `services/property_watches.py:406-415`); a bootstrap's draw is outside it by design (§H.4.4).
12. **Training loads ORM entities for up to 806,000 metric rows** (`training.py:77-80`); PW-3b
    recorded the bound as "not measured on a console". Bootstrap reaches it on day 1 (§H.8.1).
