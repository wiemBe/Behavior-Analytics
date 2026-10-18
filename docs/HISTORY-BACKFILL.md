# History backfill: every baseline reads its past from Ariel once, for learning only

Status: **design, 2026-10-18.** Not built. It generalises [`PHASE-G-HISTORY-BOOTSTRAP.md`](PHASE-G-HISTORY-BOOTSTRAP.md)
(cited as §H.n, the model for property watches) to the platform-wide baselines, and changes none of its text.
It rests on [`SEARCH-CADENCE.md`](SEARCH-CADENCE.md) (§SC.5, §SC.6, the 5-minute default), on
[`PRODUCT-UX-GUIDELINES.md`](PRODUCT-UX-GUIDELINES.md) (§4, §6, §8, §10) and on decision D-31.
Every claim about the code carries a `file:line` citation checked on this branch (`agent/hb0`, from
`feat/phase-ce`) on 2026-10-18. Where the code and a family's design text disagree, the code wins and the
disagreement is listed in §B.14.3. Facts that only the console can give are not asserted: each has a
probe in §B.3.5 and is marked **[C-n]**.

> **The user's decisions (2026-10-18 ~22:45):** build it as designed, with one shared core for this
> backfill and the property-watch bootstrap; WAF and IPS read history only for sources first collected
> after the answer; the load of a large install is accepted, with the window fitted to 72 hours and
> said so. The remaining open questions take this design's recommendations.

## B.0 What was asked, and what the backfill is not

The operator is asked once, right after installation, whether the platform should search the past so
that baselines and robust deviation (MAD) are steadier from day one. No: nothing changes. Yes: every
baseline family fills its **existing** window from QRadar's stored history, for learning only.

| # | Decision (D-31, 2026-10-18) | Where |
|---|---|---|
| B1 | The windows do not change. The backfill fills `baseline_lookback_days` (28, `backend/app/core/config.py:336`), `ips_baseline_lookback_days` (28, `:187`) and the rule-health window (30, `:298`) at once. Reading further back is wasted: retention would delete it (35 days for WAF, IPS, catch-all, `:488,:182,:521`) | §B.3.1 |
| B2 | The answer is **No / Yes, where Yes reads the last 28 days**. There is no month choice. A family whose own window is not 28 days reads its own window (rule analytics: 30) | §B.3.1 |
| B3 | Two places ask: **`./qradar-dash install`, once, on the first install**, and the admin UI (a first-run dialog when the installer did not ask, and an admin page for later) | §B.9, §B.10 |
| B4 | Every baseline takes part: source volume (Phase A), WAF, IPS, the catch-all known-senders list, and rule matches. Geo is left out, with a reason | §B.2 |
| B5 | A history row is **learning only**: never evaluated, never opens an incident, a breach or an alert. Each family has three independent guards (§B.4) | §B.4 |
| B6 | Property watches keep their own per-watch bootstrap (D-29). The platform answer does not govern them. They share the core of this design | §B.11 |
| B7 | The reading is oldest first, in bounded steps, inside the family's own task and lock, after the live pass, on what is left of the tick budget (live first) | §B.6 |
| B8 | How far back Ariel holds a scope is proved, not assumed (§B.3.2). Nothing before the proved floor is written: not a zero, not a placeholder row | §B.3 |
| B9 | A later Yes is decided per family (§B.5): allowed for Phase A, catch-all and rule matches (no verdict depends on admission order); for WAF and IPS only for a scope with no live bucket yet | §B.5 |

### B.0.1 What it is not

- **Not an evaluation of the past.** No detector, lifecycle, rule-health flap counter or custom rule
  judges a history bucket. The rule of [`SEARCH-CADENCE.md`](SEARCH-CADENCE.md) §SC.6 ("an upgrade must not
  replay history into alerts") holds in full for every family.
- **Not fabricated history.** A bucket is written only from a whole Ariel search that measured it. A span
  Ariel cannot be proved to hold, a failed or truncated search, a property that did not parse: each
  yields no row, or a row that says what it is. Never a zero, never `NORMAL` (repository rule 8).
- **Not a second collector.** Each family's history step calls the family's own pure fold and admission
  (`fold_groups`, `decide_admission`, `fold_source_bucket`, `fold_outcome`) in time order. Only the
  persistence differs, because the live persistence costs statements per bucket (§B.8).
- **Not a write to QRadar.** Only existing read-only keyed Ariel searches, on the REST provider, built
  from stored templates (repository rule 1, 2, 4). No AQL comes from the browser.
- **Not raw storage.** Rule 3 is unchanged: only the aggregates each family already persists leave the folds.

## B.1 The model in one picture

```text
decision (per console)   NO | YES(28 d)   recorded by the installer, or by an admin in the UI, write-once per answer
scope (per family)       one row per (console, family, scope key): Phase A -> the console; WAF/IPS -> one source;
                         catch-all -> the console; rule matches -> the console
history_state            NOT_REQUESTED | WAITING | READING | COMPLETE | PARTIAL | UNAVAILABLE
marker on every row      origin = 'HISTORY'  (Phase A: collection_source = 'ariel_history', see §B.4.1)
anchor / hand-off        the first LIVE bucket of the scope; history ends exactly there: zero gap, zero overlap
```

## B.2 Which families take part, and which do not

| Family | In? | Why |
|---|---|---|
| **Phase A source volume** | yes | `BaselineBuilder` needs `baseline_min_samples` = 8 clean samples per weekday x hour cell (`config.py:335`) over the 28-day lookback (`app/anomaly/baseline.py:96-137`). Without history a cell fills one week at a time, so a fresh install has no verdict for 1 to 4 weeks (SEARCH-CADENCE §SC.1) |
| **WAF** | yes | Endpoint request baselines, the ratio baselines and the new-endpoint claim all read stored buckets (`app/waf/baseline.py:370`, `app/waf/engine.py:483-602`) |
| **IPS** | yes | The same shape: entity baselines over `ips_baseline_lookback_days` (`app/ips/baseline.py:296,374`) and the new-port novelty window (`app/ips/engine.py:515-540`) |
| **Catch-all senders** | yes | The "known senders" list is the baseline: a sender that has a row is never NEW again (`app/collectors/catchall_collector.py:382-456`). History makes the list complete at once, so fewer senders are wrongly NEW |
| **Rule analytics** | yes, **rule matches only** | Rule health judges `NEVER_OBSERVED`/`INACTIVE` only when **every** window of the 30-day span is `COMPLETE` (`app/rules/matches.py:266-312`, `app/services/rule_health.py:357-420`). Today that takes 30 days after enabling. History makes it provable at once. The offense-derived `rule_metric` collector (`rule_metric_lookback_days` = 30, `config.py:274`; `app/collectors/rule_metric_collector.py:88`) reads our own database, not Ariel: nothing to backfill. Rule matching is **off by default** (`config.py:278`), so the family is offered only where it is enabled |
| **Geo** | **no** | `geo_country_bucket` has no baseline, no detector, no admission and no verdict: it is a display aggregate (`app/collectors/geo_collector.py:1-30`, `docs/DATA-MODEL.md` "Geography"; the widget that consumes it is not built, PRODUCT-UX-GUIDELINES §11). History would only fill a chart. It is also the heaviest search shape (a whole-estate `GEO::LOOKUP`, unmeasured at 28 days). Left out; one line on the admin page says so. Open question Q8 |
| **Property watches** | own bootstrap | §B.11 |

## B.3 The window and the floor proof

### B.3.1 How many days

`history_backfill_days` is 28 by default and is bounded by the family's own lookback and retention:

| Family | Window read | Bound | Code |
|---|---|---|---|
| Phase A | 28 d | `baseline_lookback_days` | `config.py:336` |
| WAF | 28 d | `baseline_lookback_days` (WAF has no lookback of its own); retention 35 d must exceed it (existing validator) | `config.py:488,882-886` |
| IPS | 28 d | `ips_baseline_lookback_days`; retention 35 d | `config.py:182,187` |
| Catch-all | 28 d | `catchall_retention_days` 35 d (buckets); senders have no lookback, only `catchall_sender_active_days` 7 | `config.py:514,521` |
| Rule matches | 30 d | `rule_health_window_days` | `config.py:298` |

Three hard bounds apply to every family:

1. **Retention.** The window plus the reading time must stay below the family's retention, or the daily
   retention task deletes what was just read. Window 28 d plus `history_backfill_max_hours` (72 h) is 31 d,
   below 35 d.
   - **Phase A is different:** `log_source_metric` retention is **off by default**
     (`retention_enabled = False`, `retention_log_source_metric_days = None`, `config.py:219-222`;
     `app/services/timescale.py:27`) and no validator ties it to the baseline lookback. A site that enabled
     it with fewer than 31 days would delete history as it is read. The planner reads the setting and
     reduces the Phase A window to `retention - 3 d`, or refuses with the reason (§B.12 row 3).
2. **168 h per search.** `validate_bucketed_metric_window` refuses a longer window
   (`app/collectors/bucketed_metrics.py:28-29`).
3. **120 s per search.** The poll loops end at 120 one-second polls and cancel
   (`app/providers/qradar_rest.py:760-767`, `:2114-2121`). Wide windows must stay below it **[C-5]**.

### B.3.2 How the design knows, read-only, how far back Ariel holds a scope

The question and the rule are those of §H.3.2: a bucket with no row in a whole search is either measured
silence or aged out, and the two look the same. **A bucket is history only if an event of the same scope
exists at or before it, plus a margin `m`** (`history_backfill_floor_margin_hours`, 24).

The scope kind decides what "an event of the same scope" is, and where the proof comes from. In every
family the proof is **derived from the read itself**, so it adds no search:

| Family | Scope kind | Proof | Floor |
|---|---|---|---|
| Phase A | one log source (`logsourceid`) | the first bucket in which the source has a row in the keyed read | `F_s = max(T0, first_event_bucket_s + m)` **per source** |
| WAF | one log source (`logsourceid = N`, `qradar_rest.py:1253-1255`) | the second statement of the keyed volume search is a per-bucket `SUM(eventcount)` total (`:1266-1272`): the first bucket with `total_events > 0` | `F = max(T0, b0 + m)` per source |
| IPS | one log source | the `TOTALS` dimension (`app/ips/keyed_search.py`, `IpsDimension.TOTALS`) | `F` per source |
| Catch-all | a set of device types (`catchall_type_ids`, `config.py:501`), with several catch-all log sources inside | the keyed read carries `logsourceid` on every row (`catchall_collector.py:317-326`): the first bucket in which a catch-all **source** has a row | `F_s` per catch-all source |
| Rule matches | the whole estate | the keyed `creEventList` read is per rule, so it cannot show that the console held the hour. A **console-wide total per window** is added to the first span (one extra search; the 1-search cost is in the table of §B.8) | one `F` for the instance |

Each read starts `m` before `T0` (`T0 - m`), so an event in the margin proves the first window bucket.
Rules:

1. **Nothing before `F` is written.** The shared "nothing before the floor is written" rule:
   no zero row, no `NOT_COLLECTED` row, no placeholder. A missing row is "no claim"
   (PRODUCT-UX-GUIDELINES §4 MISSING).
2. **A scope with no event in the whole window** is `UNAVAILABLE`: nothing is written, and the live
   path is exactly what it is today.
3. **Phase A and catch-all do not zero-fill history before the floor.** Live zero-fill writes a row for
   every monitored source absent from the result (`metric_collector.py:406-491`, monitored filter
   `:447-456`; catch-all `catchall_collector.py:321-326`). In history that zero would be fabricated for
   a source that did not exist yet or whose events aged out. History zero-fills a source only from its
   own `F_s` on, which is why Phase A is read **oldest first** (§B.6.2): `F_s` is known when the first
   event is read.
4. A source whose first event is late in the window loses its leading silence. That is conservative and
   true to what is provable.

| Assumption | If false | Covered by |
|---|---|---|
| A1: deletion is oldest-first inside the scope's retention class **[C-2]** | The totals reach back but a short-retention class is missing earlier, and a window could look complete while missing a class | probe P-3; the page states "assumes one retention policy per scope"; the production default stays No until it is read |
| A2: the counts of a bucket near the floor are whole **[C-4]** | The cut bucket is partial and would be stored exact | the margin `m`; probe P-7 |
| A3: the property is extractable from old events **[C-3]** | Payload retention shorter than event retention; an indexed property extracted at ingest only | coverage per bucket (§B.12 row 4): WAF stores `endpoint_coverage_ratio`, IPS `*_coverage_ratio`, both already read by the detectors |
| A4: counts equal at search time and at original collection | A late event changes an old bucket | none needed: history is read once |
| A5: today's mapping applies to the past | A profile, a field mapping or a catch-all type set changed since | stated limitation; history buckets carry the **current** `profile_version` (§B.7.1) |

### B.3.3 Where the proof is stored

`history_floor` per scope (for Phase A and catch-all, per source in `history_progress.floors`, a bounded
map, never a value text), `history_target_start` (`T0`), counts in `history_progress` (§B.7.1).

### B.3.4 Asked days versus proved days

`COMPLETE`: `F <= T0 + one bucket` for every scope and no hole. `PARTIAL`: Ariel holds less than asked, or a
hole exists, or the reading stopped on a limit or the switch. `UNAVAILABLE`: no event in the window, or the
floor scan could not be done. None of the three is an error and none is health.

### B.3.5 Probes: the console facts this design cannot assert

One read-only lab task (HB-9, §B.13), a throwaway script outside the repository, the REST provider, MCP
never, sequential, at most 60 Ariel searches and 8 other GETs. It extends the probes of §H.3.5 (P-1 to
P-8) with the scope kinds of this design:

| Probe | Request | What each answer means |
|---|---|---|
| **P-1 age of every scope kind** (central) | daily `SUM(eventcount)` over 35 days in 5 spans of 7 days, once per kind: a Phase A source, a WAF source (`logsourceid = N`), an IPS source, the catch-all types, and unscoped (10 + 5 searches) | leading days without a row: the scope is young **or** aged out **[C-1]** |
| **P-2 console-wide floor** | the unscoped daily search above | tells "aged out" from "never existed" for the lab, and is the rule-match proof |
| **P-3 retention configuration** | `GET /api/help/endpoints` filtered for "retention" (1 GET) and the endpoint it names | the number of retention buckets and their filters (A1, **[C-2]**) |
| **P-4 property on old events** | per day, COUNT of events and of events with the WAF URL property and the IPS destination port property `IS NOT NULL`, 35 days | payload expiry or a new property (A3, **[C-3]**) |
| **P-5 time against width and rows** | for each family's real shape: Phase A (`logsourceid, starttime/300000`), WAF volume (url, method, vhost), catch-all (`logsourceidentifier`), rule match (`creEventList`, `starttime/3600000`) at 1 h, 6 h, 24 h, 72 h and 168 h, `LIMIT 5001` (20 searches) | seconds and rows per search for the cost tables **[C-5]** |
| **P-6 repeat** | the 24 h window of P-5 three times | cold against warm (T-040 saw a 3.5 s first call, SEARCH-CADENCE §SC.10) |
| **P-7 the ramp at the floor** | only where P-1 finds a floor inside 35 days: hourly totals for 3 days around it | whether the first day is partial **[C-4]**; sets the margin |
| **P-8 the 120 s edge** | the 168 h Phase A window on the busiest lab state | whether the widest span times out |
| **P-9 the 4 s per-source reading of a large fleet** | `SELECT logsourceid, MIN(starttime) ... GROUP BY logsourceid` over 28 days, once | whether a one-search per-source first-event scan is cheaper than reading the first span (it would replace the derived proof for Phase A) |

Outputs are counts, seconds, row counts and dates. No value text enters the report.

## B.4 Learning only: the marker and the guards, per family

The rule of §H.2 applies to every family: a history row is evidence for later verdicts about live buckets and
nothing else. Each family gets (1) a **marker**, (2) three independent guards, (3) a classification of every
reader of its tables.

The convention: **a column `origin` (`'LIVE'`/`'HISTORY'`, `CHECK`) on the bucket table of each family whose
table may change, and `collection_source = 'ariel_history'` for Phase A**, whose tables stay as they are
(repository rule 6). A shared helper, `app/history/origin.py`, owns the two spellings (`is_history_clause(model)`),
so a reader never writes either by hand and a guard test can find them (§B.4.7).

### B.4.1 Phase A

- **What fills:** `log_source_metric`, one row per source and 5-minute bucket from `F_s`, with exactly the
  columns the live keyed path measures (`metric_collector.py:301-404`; the telemetry the live path declares,
  `:333-335`, is declared by history rows too, so `bucket_telemetry` credits them the same).
- **Marker:** `collection_source = 'ariel_history'` (the column is nullable `String(32)`, today `mock` or
  `ariel_aggregate`, `models/log_source.py:182`, `metric_collector.py:91-96`), and
  `query_provenance.history = true` with `floor` for a human. No column is added: the table is Phase A's.
  The mock provider writes `mock_history`.
- **Writes never overwrite a live row.** History inserts are `ON CONFLICT DO NOTHING` on
  `(log_source_id, bucket_start)`; the live upsert (`:396-404`) stays as it is and may replace a history row
  (it is then a live row, which is correct).
- **Guard 1, ordering.** History ends at the **anchor**: the oldest live bucket of the console when the plan
  is made. `evaluate_pending` takes only `bucket_start > evaluated_through` (`app/anomaly/engine.py:205-217`),
  and a source's watermark is at or after its oldest live bucket, so an older bucket is never pending.
- **Guard 2, the first-run branch.** A source with no watermark takes its **latest** metric
  (`engine.py:173-196`). History is older than every live bucket, so the latest is live, **and** history for
  a source is planned only once the source has at least one live bucket (§B.6.2). Written as a condition so a
  refactor cannot break it: the first-run query adds `collection_source IS DISTINCT FROM 'ariel_history'`.
- **Guard 3, the column.** Both queries of `evaluate_pending` (the first-run and the pending read) add the
  same predicate, so a watermark written wrongly (restored backup, manual SQL) still cannot make a history row
  pending. A test inserts a history row after the watermark and with no watermark, asserts nothing is evaluated,
  then removes the predicate and watches it fail (the repository test rules).
- **Streaks.** `_preceding_empty_buckets` (`engine.py:329-356`) counts consecutive complete zero buckets; a
  `NO_EVENTS` incident's `anomaly_start` is the judged live bucket (`engine.py:592`). History zeros are real
  measured silence, but a silence claim built partly from history would be a verdict that leans on an old
  event. Decision: the streak counts **live rows only**. Cost: `NO_EVENTS` waits `silence_grace_buckets`
  (`thresholds.py:36`) live buckets after the anchor. Open question Q6.
- **Readers.** Every reader of `log_source_metric` is classified (guard test, §B.4.7):

| # | Reader | Kind | Treatment of history |
|---|---|---|---|
| R1 | `evaluate_pending`, both branches (`anomaly/engine.py:166-246`) | LIVE only | `collection_source IS DISTINCT FROM 'ariel_history'` |
| R2 | `_preceding_empty_buckets` (`:329`) | LIVE only | same predicate |
| R3 | `_recent_signatures` (`:377`) | EVIDENCE | reads both; history rows carry no signature (live bucketed rows carry none either) |
| R4 | `BaselineBuilder._load_clean_observations` (`baseline.py:139-165`) | EVIDENCE | reads both, **on purpose**: this is the learning |
| R5 | F.1 `load_log_sources`, `load_fleet_classes` (`custom_rules/loaders.py:64-105,107-182`) | LIVE only | the predicate. The F.1 engine seeds a kind's watermark when the kind has no watermark (`engine.py:266-282`) but a kind that already has one reads everything after it, so a history row after that watermark would be judged. The preview may read history (replay, persists nothing) |
| R6 | fleet series, API charts (`fleet/series.py:108-137`, anomalies route `api/routes/anomalies.py:664`) | DISPLAY | reads both and labels history in text ("read from QRadar history, not judged") |
| R7 | retention (`timescale.py`) | DELETE | by chunk time |

- **Hand-off:** none to the collector. `CollectionWatermark` is untouched: live runs on, never paused. The state
  row records `history_end = anchor`. Zero gap and zero overlap: history ends at the anchor (exclusive) and the
  live collector starts at `watermark + interval` (`metric_collector.py:188-194`).
- **Order-dependent admission:** **none.** Phase A has no admission (it zero-fills the inventory of monitored
  sources, `:439-456`). The order matters only for the floor proof, hence oldest first.
- **After the reading:** a baseline rebuild for the sources just read, in chunks per tick (§B.8.5), because the
  daily beat (`celery_app.py:69-73`, 86 400 s) would otherwise make the history useless for a day.

### B.4.2 WAF

- **What fills:** `waf_source_bucket` (one per bucket), `waf_endpoint_metric` (sparse), `waf_endpoint` rows, in
  two passes (§B.7.3).
- **Marker:** `waf_source_bucket.origin`, `String(8) NOT NULL`, `CHECK`, additive migration, backfilled `'LIVE'`.
  `query_provenance.history` for a human. Endpoint metrics are read through their source bucket (the engine
  judges a source bucket and joins its metrics), so one marker is enough.
- **Guard 1, watermarks.** While a source is `READING` its collection watermark and its evaluation watermark are
  both unset. `_evaluate` returns when the collection watermark is NULL (`waf_pipeline.py:271-272`).
- **Guard 2, the seed.** The pending query takes **every** bucket up to the collection watermark when the
  evaluation watermark is NULL (`waf_pipeline.py:273-280`); that is safe today only because a first run writes
  one bucket. At hand-off both watermarks are written to the last history bucket (the evaluation row is a
  `CollectionWatermark` with collector `waf_endpoint_evaluation:<id>`, `waf_pipeline.py:56-58,186-194`), so
  `bucket_start > evaluation watermark` excludes every history bucket.
- **Guard 3, the column.** `_evaluate` adds `WafSourceBucket.origin == 'LIVE'`; so does the F.1 loader
  (`custom_rules/loaders.py:185-276`).
- **The novelty guard.** An endpoint whose `first_seen_at` is a history time and within 12 buckets of the
  hand-off would make the live buckets after hand-off "inside the novelty window"
  (`waf/engine.py:483-515`: `bucket_start >= first_seen_at + window` is the only exit), so a first appearance
  that happened during history could open `WAF_NEW_ENDPOINT_ACCESS` from a live bucket. Rule: **an endpoint
  with `first_seen_at < history_end` is established**, whatever its age, exactly like §H.1.3. This is the one
  change to a detector input in this design; it is one condition in `_load_novelty` and one in the detector's
  `within_window`.
- **Hand-off:** zero gap, zero overlap, as §H.2.4: the moving end. History reads to the closed-bucket boundary
  of its own tick; the tick that reaches it sets the two watermarks to `boundary - bucket` and the live
  collector starts at `boundary` (`waf_endpoint_collector.py:457-474`). Live collection of that source is
  paused while it is `READING`; the pause is of a scope that, being new, had nothing live to pause (§B.5).
- **Order-dependent admission: yes.** `decide_admission` is stateful across buckets: the active count, the
  per-bucket quota and `first_seen_at`/`tracked_since` all depend on order (`waf/admission.py`,
  `waf_endpoint_collector.py:806-880`). Hence oldest first, and no prepend (§B.5).
- **Retirement:** the live path retires with the wall clock (`:1138-1166`, `cutoff = now - 7 d`,
  `tracked_until = last_collected`). History retires **per bucket in memory** with the bucket time as the
  reference, so the active ceiling and the tracking windows are what bucket-by-bucket collection would have
  produced. Without this a 28-day read keeps long-dead endpoints against the 2 000 ceiling
  (`config.py:455`) and the ceiling binds early.

### B.4.3 IPS

- **What fills:** `ips_source_bucket` (with the contributor lists, a bounded top list by rule 3),
  `ips_entity_metric`, `ips_entity`.
- **Marker:** `ips_source_bucket.origin`, as WAF.
- **Guards:** identical in shape. `_evaluate` returns when the collection watermark is NULL and otherwise takes
  every bucket when the evaluation watermark is NULL (`ips_pipeline.py:258-266`); hand-off seeds both; the
  pending read adds `origin == 'LIVE'`; the F.1 loaders `load_ips_entities` and `load_ips_sources`
  (`loaders.py:278-425`) add it too.
- **The novelty guard:** `IPS_NEW_PORT` has the same exit (`ips/engine.py:515-540`); the same
  `first_seen_at < history_end` rule applies.
- **Retirement and absence:** the live path retires with `now` and no `tracked_until`
  (`ips_collector.py:586-599`); a retired entity's absent bucket reads as unknown, not zero
  (`ips/inputs.py:262-263`). History retires per bucket in memory (the ceiling is 2 000 per source and
  dimension, `config.py:197`).
- **Hand-off and order dependence:** as WAF (moving end; admission is order-dependent, `ips_collector.py:625-730`).
- **Fleet shape:** IPS collection plans every eligible source together and shares keyed searches
  (`ips_collector.py:227-311`). History is planned per **group** of sources that share a plan, so a group of
  new sources reads together; a group is at most `ips_max_sources_per_search` (50, `config.py:171`).

### B.4.4 Catch-all senders

- **What fills:** `catchall_bucket` (counts and a bounded `top_senders` list) and `catchall_sender` rows.
- **Marker:** `catchall_bucket.origin`; a sender row has no bucket provenance, so it carries
  `reviewed_by = 'history'` (existing column, `models/catchall.py`) beside the existing `'bootstrap'`.
- **What "evaluation" is here:** nothing reads catch-all history as a verdict. The only consequence is a
  **NEW** sender, which raises an alert only when `catchall_new_sender_alerts_enabled` (off by default, D-9,
  `config.py:517`) and only in `_write` for senders inserted in that run (`catchall_collector.py:420-456`).
- **Guard 1, the status.** Every sender admitted from history is `REVIEWED`, never `NEW`. The code already has
  this switch: `report.bootstrap` (`:157`, `:419-420`). History forces it on.
- **Guard 2, no alert from a history step.** The alert call (`:200-202`) is not reached from a history step.
- **Guard 3, the hold.** While the console's catch-all history is `READING`, the **live** collector also runs
  as a bootstrap run: a sender first seen live in that time is `REVIEWED`, not `NEW`. Otherwise a sender that
  appears live at hour 1 and in history at day 12 would be created NEW by the live run and then seen by
  history as already known. The metadata key `bootstrapped` (`:171-176`) stays false until hand-off.
- **Hand-off:** none to a watermark that must be paused: the live collector keeps running (prepend is safe, §B.5);
  history ends at the anchor, the oldest live bucket. The state row records `history_end`.
- **Order-dependent admission: yes, but it cannot change a verdict.** The ledger and the per-bucket quota are
  stateful (`catchall/fold.py:48-52`, `catchall_collector.py:239-299`), so the order decides **which** senders
  hold the 2 000-per-source ceiling; no detector reads that. History therefore seeds its ledger with the
  currently active senders (`_load_senders`, `:572-588`) and reads the pre-anchor range oldest first. A sender
  created NEW before the Yes keeps its status; its `first_seen_at` becomes the earlier time
  (`LEAST(...)`, `:437-442`), which is true.
- **Retirement:** `_retire_stale` (`:495-507`) retires by wall clock at the start of the next live run. History
  writes `active = (last_seen_at >= anchor - catchall_sender_active_days)` so a long-gone history sender does
  not hold ceiling slots, and the retention task (`services/catchall_retention.py:34-48`) later deletes
  inactive rows by `retired_at`.

### B.4.5 Rule matches

- **What fills:** `rule_match_window` (one per hour: status, `rows_returned`) and `rule_match_metric` (sparse).
- **Marker:** `rule_match_window.origin`. The metric rows are read through their window.
- **Evaluation:** rule health is not a pending-bucket engine. It judges **now**, from the stored windows of the
  span (`rule_health.py:357-420`), and it has no alert path (nothing under `app/alerts` or `app/custom_rules`
  reads `RuleHealthSnapshot`, which this design verified). History windows are exactly the evidence it needs, so
  all readers **read both**. The learning-only property is that the verdict is about the rule today.
- **The one guard that is needed:** `RuleMatchWindow` rows are inserted `ON CONFLICT DO NOTHING`
  (`rule_match_collector.py:289-296` already does this for skipped windows), so a history step never replaces a
  live window, and a `FAILED` skipped-window record written by the live horizon rule
  (`_skip_beyond_horizon`, `:256-310`) is replaced by a history window only through a deliberate
  `DO UPDATE ... WHERE status = 'FAILED' AND origin = 'LIVE' AND error = SKIPPED_REASON` (a window that was
  never searched becomes a measured one).
- **Hand-off:** the anchor is the oldest live window; the live first run reads only the latest settled window
  (`:338`). Zero gap, zero overlap, and a window the history could not prove is **absent**, which
  `assess_coverage` counts as `missing` (`matches.py:297-298`): the verdict stays `INSUFFICIENT_DATA`.
- **Order-dependent admission: none.**
- **COMMON rules** keep their meaning (a lower bound, never silence) because the fold is the live one.

### B.4.6 What the readers of every history table share

A history row is **evidence**. A reader falls into exactly one kind: LIVE-only (a pending read, a streak, an F.1
loader), EVIDENCE (a baseline builder, a novelty history, a component lookup), DISPLAY (an API that labels
`origin`), DELETE (retention). The classification of each family is in its own subsection; §B.4.7 turns it into
a test.

### B.4.7 The guard test

`tests/unit/test_history_readers_are_classified.py` scans the repository for every `select` over the history
tables (`log_source_metric`, `waf_source_bucket`, `waf_endpoint_metric`, `ips_source_bucket`,
`ips_entity_metric`, `catchall_bucket`, `rule_match_window`) and fails when a reader is not in the classified
list or a LIVE-only reader lacks the helper clause. It is in the shape of `implemented_loaders()`
(`custom_rules/loaders.py:41`). Phase 1 of each adapter adds its readers to the list **before** the history
writer exists, so the guards precede the data.

## B.5 When the backfill may run, and a later Yes

The installer asks before the first collection (§B.9), but a Yes can also come later from the UI (§B.10), and a
site that skipped the installer prompt has live data already. The rule is per family, and it follows one
question: **does any verdict depend on the order in which admission saw the data?**

| Family | Admission order matters? | Run at install (no live data) | Later Yes, live data exists | Why |
|---|---|---|---|---|
| Phase A | no admission | reads from the first live bucket on (WAITING until it exists, one tick) | **allowed, prepends**, per source | Nothing is stateful: a prepend adds rows before the anchor. Sources added later are topped up by "Read history now" on the admin page |
| Catch-all | stateful, but no verdict depends on it (§B.4.4) | same | **allowed, prepends**; live runs in bootstrap mode while it reads | Worst case a sender that history would have called known was NEW before the Yes |
| Rule matches | no admission | same | **allowed, prepends** | Windows are independent; absence is a measured `missing` |
| **WAF** | **yes**: novelty, `first_seen_at`, `tracked_since`, the active ceiling | the scope does not exist yet: it is created by a profile; reads at its first collection | **only for a source with no live bucket** (a profile created after the Yes, or one never collected) | A prepend would move `first_seen_at` of endpoints used by evaluated buckets and by open incidents (§H.0.3) |
| **IPS** | **yes**, as WAF | same | **only for a source with no live bucket** | same |
| Property watches | yes | own rule (D-29) | own rule | §B.11 |

What this gives the operator, in the installer-Yes case: WAF and IPS collection are **off by default**
(`config.py`; the installer summary says "Left off WAF, IPS, rule-match collection", `qradar-dash:839`), so no
WAF or IPS scope exists at install. The answer is stored per console and **applies to every WAF or IPS source
the first time it is collected**, whatever the date. A Yes at install therefore reaches WAF and IPS fully, and
so does a Yes given in the UI later, for every source the operator has not yet enabled.

A WAF or IPS source that **already has live buckets** and was never read cannot be backfilled in this build.
The admin page says so per source, with the reason, and the remedy is to disable the profile and create it again
(the existing way to start a scope fresh), after which it reads. A prepend for such a source is a separate
design (Q3).

A "No" can be turned into a "Yes" at any time, and a "Yes" can be stopped (the reading ends `PARTIAL`, reason
"stopped by <user>"); a "Yes" is never reversed (history rows are not deleted by this feature).

## B.6 The budget: live first, then history

### B.6.1 Inside the family's own task and lock

Each family already runs in a task that holds an advisory lock for its scope, takes one cadence slot and has a
per-tick search cap (`search_cadence_max_searches_per_tick`, 16, `config.py:159`). The history step runs
**inside that task, after the live pass, under that lock**:

| Family | Task | Lock held | Live budget | History sub-budget |
|---|---|---|---|---|
| Phase A | `collect_metrics` (`workers/tasks.py:97`) | (instance, `log_source_metric`) (`metric_collector.py:104`) | 1 keyed search | `min(history_backfill_searches_per_tick, 16 - used)` |
| WAF | `collect_waf_source` per profile | per source pipeline lock (`waf_pipeline.py:56,214-219`) + the global Ariel permit (`waf_ariel_budget.py`) | 2 + B | same |
| IPS | `dispatch_ips_collection` | per source fleet locks (`ips_collector.py:153-210`) | `P` | same, shared `IpsSearchBudget` |
| Catch-all | `collect_catchall_senders` | (instance, `catchall_senders`) | 1 | same |
| Rule matches | `collect_rule_matches` | (instance, `rule_match`) | 1 | same |

Reasons: (1) the operator's one control over Ariel schedule (D-7, the cadence) holds with no new task and no
new `SearchFamily`; (2) **live first** is true by construction: a history step only spends what the live pass
left; (3) one lock per scope means a history step and a live run of the same scope can never overlap, which the
property-watch dispatch lacked (`workers/tasks.py:1394-1442`, §H.14.3 item 9).

Settings (all in `config.py`; validated only when the family's own collection switch is on):

| Setting | Default | Bounds and notes |
|---|---|---|
| `history_backfill_enabled` | true | kill switch; off: no step runs, a scope reading hands off `PARTIAL` "switched off" |
| `history_backfill_days` | 28 | `>= 7`, `<=` the family's lookback (§B.3.1) |
| `history_backfill_searches_per_tick` | 10 | `1..64`; the sub-budget above |
| `history_backfill_seconds_per_tick` | 120 | `10..600`; no history search starts after it; below the Celery visibility of a family task |
| `history_backfill_max_hours` | 72 | `1..168`; `days x 24 + this < retention x 24` |
| `history_backfill_floor_margin_hours` | 24 | `1..168` |
| `history_backfill_max_failures` | 3 | consecutive failures at the minimum width before a gap |
| `history_backfill_max_lower_bound_share` | 0.05 | the density gate (§B.6.4) |
| `history_backfill_waf_breakdown_days` | 7 | days of breakdown history (§B.7.3) |

### B.6.2 A step, per family, in one rule

Every family's step is the same pure function of `(state, boundary, anchor, budget, settings)` in
`app/history/planner.py`:

```text
1 scan    when F is unknown: the first span starts at T0 - m (this is the floor proof, no extra search)
2 span    [J + 1 bucket or the span start, s) with s = min(end, start + min(2016, rows-fit * W))
3 run     the family's keyed searches over the span, W buckets at a time (rows <= 80 % of the row cap)
4 fold    the contiguous completed prefix, oldest first, with the family's own pure fold
5 write   one bulk statement per table (§B.8): rows with origin = HISTORY, state, progress
6 adapt   W := clamp(floor(0.8 * row_cap / (1.25 * rows per non-silent bucket seen)), 1, 2016); halve W on FAILED
7 finish  when the prefix reached the end (the anchor, or the moving boundary): hand off (§B.4.n)
```

`end` is the **anchor** for Phase A, catch-all and rule matches (a fixed oldest live bucket) and the **moving
boundary** for WAF and IPS (the closed-bucket boundary of the finishing tick). "Row cap" is
`ariel_multi_bucket_max_rows` (5 000, `config.py:162`) for Phase A, WAF and IPS, `catchall_max_rows_per_search`
(2 000, `:505`) for catch-all and `rule_match_max_rows` (2 000, `:296`) for rule matches.

Phase A, catch-all and rule matches are planned from the **anchor**: a scope that has no live bucket is
`WAITING` (one tick). WAF and IPS scopes are planned at their first collection.

### B.6.3 Failure is not truncation

The family drivers' rules are kept: a **truncated** multi-bucket search keeps nothing and is split in time; at
one bucket the result is a lower bound (`ips/keyed_search.py` docstring, `metric_collector.py:247-257`,
`waf_endpoint_collector.py:515-610`). A **failed** search is not split (retrying a failing appliance multiplies
the load). In history a failed span halves `W` for the next tick (a timeout is a function of width); after
`history_backfill_max_failures` failures at the minimum width the span becomes a recorded **gap** (`history_progress.gaps`,
at most 20 entries, no value text), no row is written for it, `J` moves past it, and the state ends `PARTIAL`.
A gap fails every novelty or `COMPLETE` coverage claim across it through its missing buckets.

### B.6.4 The density gate

After the first span, if more than `history_backfill_max_lower_bound_share` of the buckets read came back as
lower bounds (a bucket with more rows than the cap even alone), the scope ends `PARTIAL` with "more than N
distinct values per 5-minute bucket: history would be lower bounds". The buckets read stay (valid evidence for
volume).

### B.6.5 The window is fitted to the budget

At planning, the planner computes the estimate of §B.8 for the scope from the first span's observed density.
If `ceil(searches / sub-budget) x cadence > history_backfill_max_hours`, the window is **reduced to the largest
`D'` that fits**, never below 7 days (one visit per hour-of-week), and `history_reason` says "window reduced from
28 to 20 days: the estimate exceeded 72 hours". Below 7 days the scope is `UNAVAILABLE` with the estimate. The
reduced window always ends at the anchor or the moving boundary, so what is read is the **newest** `D'` days.

## B.7 State, resumption and idempotence

### B.7.1 Tables (additive migration after `0029`)

Migration `0030_history_backfill` (the number follows the property-watch foundations' `0029`, which is being
built; the order is an assumption stated in §B.13).

**`history_backfill_decision`** (one row per console, written once per answer):

| Column | Type | Meaning |
|---|---|---|
| `instance_id` | UUID PK, FK `qradar_instance` | |
| `answer` | `String(3)` `CHECK IN ('YES','NO')` | |
| `days` | `Integer` | 28 for YES |
| `decided_at` | `DateTime(tz)` | UTC |
| `decided_by` | `String(255)` | the principal subject, or `installer` |
| `source` | `String(16)` `CHECK IN ('INSTALLER','UI')` | |
| `updated_at`, `updated_by` | | a later NO to YES change |

**`history_backfill_scope`** (one row per console, family and scope key; created lazily when a scope becomes
eligible):

| Column | Type | Meaning |
|---|---|---|
| `instance_id`, `family`, `scope_key` | PK | `family` is a new `HistoryFamily` enum (`PHASE_A`, `WAF`, `IPS`, `CATCHALL`, `RULE_MATCH`); `scope_key` is `''` for a console-wide scope, the QRadar log-source id for WAF and IPS |
| `state` | `String(16)` `CHECK` | `WAITING`, `READING`, `COMPLETE`, `PARTIAL`, `UNAVAILABLE`, `NOT_ELIGIBLE` |
| `days_requested`, `days_target` | `Integer` | the fitted window (§B.6.5) |
| `target_start`, `floor`, `through`, `anchor` | `DateTime(tz) NULL` | `T0`, `F` (per-source floors for Phase A and catch-all live in `progress`), the resume point, the end |
| `started_at`, `finished_at` | `DateTime(tz) NULL` | |
| `reason` | `String(255) NULL` | words, never a value text |
| `progress` | `JSONB NOT NULL` default `{}` | `chunk_buckets`, `failures`, `searches`, `buckets_read`, `rows_written`, `gaps`, `floors` (bounded) |

Plus `origin String(8) NOT NULL CHECK (origin IN ('LIVE','HISTORY'))` on `waf_source_bucket`,
`ips_source_bucket`, `catchall_bucket` and `rule_match_window` (a temporary server default `'LIVE'`, dropped, as
`0010`, `backend/tests/integration/test_migrations.py:543`). **No migration code touches a Phase A table**
(`log_source_metric` keeps its columns, §B.4.1). No hypertable of this feature has a compression policy
(grep of `alembic/versions` finds none), so adding a column with a default is not exposed to the compressed-chunk
issue §H.11 flags; the test still adds it on a hypertable that has data.

Upgrade, downgrade and upgrade again with data carried; `alembic check` reports no drift (repository rule 7). The
downgrade deletes `origin = 'HISTORY'` rows of the four tables and the history `log_source_metric` rows
(`collection_source = 'ariel_history'`) first, because they are not reproducible without the columns.

### B.7.2 Resumption and idempotence

`through` and `progress` are written in the same transaction as the rows (the instance's one transaction,
`workers/tasks.py:206-210`), so they are exactly as far as the stored rows. A tick that fails writes nothing and
the next one re-plans from `through`. Every write is an upsert or `DO NOTHING` keyed by the table's primary key,
and `DO NOTHING` never replaces a live row. A restart of the worker mid-step is the same case.

### B.7.3 WAF in two passes

The volume pass (28 d, `waf_endpoint_metric`, `waf_source_bucket` with the three breakdown measurements set to
`NOT_COLLECTED`) is the one that decides admission and `first_seen_at`. The **breakdown pass** then reads
`history_backfill_waf_breakdown_days` (7) days of each planned breakdown (`ACTION`, `RESPONSE_CODE`, `SOURCE_IP`),
newest first, and writes only the breakdown columns of buckets whose `origin = 'HISTORY'`, honouring the live
rule that a breakdown mentions only already tracked endpoints (`waf_endpoint_collector.py:1048-1100`) and keeping
the bounded top lists. A bucket whose breakdown was not read stays `NOT_COLLECTED`, which the ratio detectors
already read as no verdict. Why two passes: the breakdown searches are the dominant cost (§B.8.2) and admission
does not depend on them.

## B.8 Cost

### B.8.1 The model

Assumptions, each stated: 5-minute buckets; window 28 d plus the 24 h margin = **8 352** buckets (rule matches:
hourly windows, 30 d plus 1 d = **744**); `rho` is the number of rows one non-silent bucket returns; a span of
`W = floor(0.8 x row cap / rho)` buckets capped at 2 016; the sub-budget is 10 searches per tick; the cadence is
5 minutes (D-28). **Measured Ariel time per search is 0.03 to 0.13 s for one bucket (PW-10 section 8), 0.37 s
for 12 buckets of Phase A, 0.61 s for an 8-hour Phase A window and about 2.2 s for 8 hours in T-040, 3.5 s for the
slowest first call ([`SEARCH-CADENCE.md`](SEARCH-CADENCE.md) §SC.2, §SC.10), and 3.7 s for a 30-day aggregate with
no bucket key (PW-10 `probe.log`).** It is **unmeasured** above 8 hours of grouped data with a custom
property **[C-5]**. The "Ariel time" column uses 1 to 3.5 s per search and is an order of magnitude, not a promise.

Three install shapes (assumed; the column `rho` names the assumption):

| Shape | Phase A sources | WAF sources (endpoints per bucket) | IPS sources (values per source and dimension) | Catch-all senders per bucket | Rules matching per hour |
|---|---|---|---|---|---|
| Small | 20 | 1 (30) | 1 (20 ports, 40 IPs, 40 signatures, 50 source IPs) | 20 | 30 |
| Typical | 100 | 3 (100) | 5 (same) | 150 | 80 |
| Large | 1 000 | 20 (300) | 30 (same) | 800 | 150 |

### B.8.2 Searches

| Family | Formula | Small | Typical | Large |
|---|---|---:|---:|---:|
| Phase A | `ceil(8352 / min(2016, 4000 / sources))` | **42** | **209** | **2 088** |
| WAF, per source: volume 28 d | `ceil(8352 / (4000 / rho))` | 63 | 209 | 643 |
| WAF, per source: action, response code (rho x 2) and source IP (rho x 5), 7 d (2 016 buckets) | `ceil(2016 / (4000 / rho_b))` each | 31 + 31 + 78 | 101 + 101 + 252 | 336 + 336 + 1 008 |
| WAF, all sources | | **203** | **1 989** | **46 460** |
| IPS: TOTALS, destination port, destination IP, signature, source IP, action, port fan-out | per dimension `ceil(8352 / (4000 / (n x rho_d)))`, `n` sources per group | 5+42+84+84+105+7+21 = **348** | 5+209+418+418+522+32+105 = **1 709** | 63+1 392+2 784+2 784+4 176+190+643 = **12 032** |
| Catch-all (row cap 2 000) | `ceil(8352 / (1600 / rho))` | **105** | **836** | **4 176** |
| Rule matches (744 windows, row cap 4 000, plus 1 console total) | `ceil(744 / (4000 / rho)) + 1` | **7** | **16** | **30** |
| **All families** | | **703** | **4 759** | **65 785** |

### B.8.3 Ticks and wall time

Ticks are per family at 10 history searches per tick (WAF: 2 concurrent sources under the global permit,
`waf_ariel_max_concurrent_sources`, `config.py:569`, so 20 per tick). Families run in parallel; the wall time is
the slowest family. Ariel time is the total searches times 1 to 3.5 s.

| | Small | Typical | Large |
|---|---|---|---|
| Phase A | 5 ticks, 25 min | 21 ticks, 1 h 45 | 209 ticks, 17 h 25 |
| WAF | 11 ticks, 55 min | 100 ticks, 8 h 20 | 2 323 ticks, 8 d 1 h: **over the 72-hour limit, the window is fitted** (§B.6.5) |
| IPS | 35 ticks, 2 h 55 | 171 ticks, 14 h 15 | 1 204 ticks, 100 h: **fitted to about 20 days** |
| Catch-all | 11 ticks, 55 min | 84 ticks, 7 h | 418 ticks, 35 h |
| Rule matches | 1 tick | 2 ticks | 3 ticks |
| Ariel time, all families | 12 to 41 min | 1.3 to 4.6 h | not meaningful: fitted |
| **Wall time** | **about 3 h** | **about 14 h** | **72 h, with reduced WAF and IPS windows** |

A large estate does not get the full 28 days in this build, by design and in the open: the dialog (§B.10.2)
prints the estimate before Yes, and the reduction is on the admin page after. Raising
`history_backfill_searches_per_tick` or `ariel_multi_bucket_max_rows` (up to 100 000, `config.py:162`) shortens
it at the operator's choice and the console's expense.

### B.8.4 Database cost

Per-run SQL stays constant (repository rule 9): the history writers add **no per-bucket, per-endpoint, per-value
or per-source statement**.

- Phase A, catch-all, IPS: the live writers already take a whole window in memory and write with one statement
  per table (`ips_collector.py:408-600`, `catchall_collector.py:382-456`); the history step calls the same
  `_persist` shape with a larger window and `origin`.
- **WAF is the exception that needs new code.** The live writer runs a lookup of existing endpoints, a count,
  a flush, a delete, an insert and a bucket upsert **per bucket** (`waf_endpoint_collector.py:753-1033`, 13
  statements per bucket, PHASE-B §29.5). That is constant per run only because a run has at most
  1 + 12 buckets. A 28-day read would be 8 352 x 13 = 108 576 statements per source. The history writer
  therefore loads the source's endpoints once (bounded by the 2 000 ceiling plus retired ones), runs admission
  and per-bucket retirement in memory over the whole span, and writes with one statement per table (the pattern
  of the IPS persist). A pin test counts statements for 1 and 10 sources, 60 and 1 200 endpoints, a 12-bucket and
  a 2 016-bucket span, and requires equality (`tests/integration/test_waf_scale.py` shape).
- Rows written (equal to the steady state at day 28, reached at day 0; PHASE-B §29.8 sizes it at 173 B per slim
  row): Phase A 161 k / 806 k / 8.1 M rows; WAF endpoint metrics at most 0.24 M / 2.4 M / 48 M rows.

### B.8.5 Training after the reading

| Family | How training runs | Ticks after hand-off (small / typical / large) |
|---|---|---|
| Phase A | a rebuild for the sources just read, `history_backfill_baseline_sources_per_tick` (50) per tick, scheduled by the hand-off (`baseline.py:96-137` is per source) | 1 / 2 / 20 |
| WAF | `waf_baseline_endpoints_per_run` = 50 per run (`config.py:482`) | 1 / 6 / 120 |
| IPS | `ips_baseline_rebuild_batch_size` = 50 (`config.py:189`) | 1 / 6 / 40 |
| Catch-all, rule matches | no training | 0 |

Until a value is trained its verdict is `INSUFFICIENT_DATA` (learning), never a guess.

## B.9 The installer asks once

### B.9.1 Where the question goes

`./qradar-dash install` (`qradar-dash`, `cmd_install` at `:650`) collects answers in `ANS`, an answers file is
read with `parse_answers` (`:166`), keys are listed in `ANSWER_KEYS` (`:84-90`), validated in `check_value`
(`:223-255` for the cross-checks, the per-key `case` above them), asked in `wizard` (`:284`) and saved by
`save_answers` (`:501`) to `.secrets/install.answers`. Order of work in `cmd_install`:

```text
1 answers (parse / wizard / validate)     :693-722
3 first writes: secrets, saved answers    :725-731
7 migrate                                 :757-765
8 break-glass admin                       :767-779
9 register the console (qradar add)       :781-786
10 start backend, worker, beat, frontend  :788-804     <- collection starts HERE
summary, then write_install_state         :815-816
```

The services that collect (`celery-worker`, `celery-beat`) start only at step 10. **So the decision can be
written before the first collection**: a new step 9b runs after `qradar add` and before step 10.

### B.9.2 The prompt

A new answer key `HISTORY_BACKFILL` (`yes` or `no`). In `wizard`, after the existing question 13:

```text
14. Read the last 28 days of history from QRadar now, so baselines are steadier from day 1? (yes/no)
    Nothing in the past raises an alert. It sends searches to QRadar for about 3 hours on a small
    estate and up to 3 days on a large one; the admin page shows the progress and the estimate.
```

- **Asked once, on the first install only.** The wizard runs only when there are no saved answers or
  `--reconfigure` is given (`:708`). The question is asked only when `ANS[HISTORY_BACKFILL]` is empty **and**
  `.qradar-dash/state.json` does not exist (`state.json` is written at the end of the first successful install,
  `:816`). A re-run reuses the saved answers (`:696-698`) and never asks. `--reconfigure` does not ask it again
  once the key is saved. **An upgrade never asks**: `cmd_upgrade` (`:2135`) does not call the wizard or
  `parse_answers`.
- **Default:** no default is offered; an empty reply is refused ("an answer is required", as `ask` does,
  `:272-276`). The default of a hand-written answers file is in B.9.3.
- `check_value` gets one case: `HISTORY_BACKFILL) [[ $val =~ ^(yes|no)$ ]]` (the pattern of `EXTERNAL_PROXY` in `check_value`).
- `save_answers` writes the key (it writes every key in `ANSWER_KEYS`), so the answer is also the "asked" marker
  of a failed first install that is re-run.

### B.9.3 The non-interactive answer

An answers file takes `HISTORY_BACKFILL=yes` or `HISTORY_BACKFILL=no` (a non-secret, like every other key;
`parse_answers` refuses a key that looks like a secret, `:189`). **An absent key is not asked and not guessed:
the console stays undecided**, the summary prints "History backfill   not decided; the first administrator
sign-in will ask (WAF and IPS read history for sources enabled after the answer)", and the UI dialog is the
entry point (§B.9.5). Rationale: a silent default of Yes spends Ariel searches the operator did not choose; a
silent No loses the history for the families that cannot prepend. Open question Q1 recommends keeping the key
optional.

### B.9.4 Where the answer is recorded

A new CLI, `backend/app/cli/history_backfill.py`, in the pattern of `app.cli.qradar` (`add`, idempotent by name,
`backend/app/cli/qradar.py:27,106-135`):

```text
python -m app.cli.history_backfill decide --instance NAME --answer yes|no --source installer
```

It runs in the backend container exactly like `qradar add` (step 9, `:781-786`), as step 9b, with
`dc run --rm --no-deps -T backend ...`. Behaviour:

- **Write-once:** it inserts `history_backfill_decision` with `source = 'INSTALLER'`, `decided_by = 'installer'`.
  A row that already exists is never changed by this command (exit 0, "already decided on <date>"). So a re-run
  of install, even with a different answer in a file, cannot change a recorded decision; a change is made in the
  admin UI by an authorised person and is audited.
- **Audit:** one `audit_log` row, `action = 'history_backfill.decide'`, `actor = 'installer'`, `detail =
  {answer, days}` (`models/identity.py:157-186`).
- **Failure does not stop the install.** If it fails or the instance row is missing (`qradar add` failed,
  `qr_rc != 0`, the row may exist: `:826-829`), the installer prints a warning, leaves the console undecided
  and continues; the UI dialog then asks. The decision write has no QRadar call and does not depend on a
  reachable console.
- A decision for a console that does not exist yet is not accepted; there is no global decision.

Because the row exists before step 10, the first collection tick sees it:

- **Phase A, catch-all, rule matches:** the scope row is `WAITING` and becomes `READING` as soon as the first live
  bucket exists (the next tick). The catch-all collector runs as a bootstrap run from its first tick.
- **WAF, IPS:** nothing exists yet. When the operator enables a source, its scope row is created `READING`
  and the first step runs in its first collection (§B.5).

### B.9.5 What the admin UI shows after the installer

The admin page (§B.10.3) reads the decision: **"History backfill: Yes, read 28 days. Recorded by the installer
on 2026-10-19 12:04 UTC."** or the same for No. The first-run dialog does **not** appear for a console that has a
decision. Per family it shows state and progress, and whether a later Yes is still possible:

| Installer answer | Phase A, catch-all, rule matches | WAF and IPS, each source |
|---|---|---|
| Yes | reading, progress | "waiting for the source to be enabled", or reading, or complete |
| No | "a later Yes is possible: reads before the first live bucket" | "a later Yes is possible while this source has not been collected; after that it is not" |

### B.9.6 When the installer is skipped

The development compose stack, a site installed before this feature, and a scripted install without the key have
no decision. The first-run dialog (§B.10.1) is the **only** entry point. It states, in plain words, what the
installer would have made certain: "WAF and IPS sources that already collected cannot read history; sources you
enable from now on will." The consequences are the table of §B.5: Phase A, catch-all and rule matches lose
nothing; the order-dependent families lose only sources that already collected. The development stack registers
a mock console (`seed`, `docs/INSTALL.md`), for which the dialog works and reads the mock provider's
deterministic history (`mock_history`).

## B.10 The dialog and the admin page

All UI follows `PRODUCT-UX-GUIDELINES` (the standing rules of the frontend quality bar): existing components and
tokens, no inline styles, text-only, every state designed, UTC with the zone, no "live"/"real-time", no
exclamation marks.

### B.10.1 The first-run dialog

- **To whom:** a principal holding `admin:history_backfill` (new permission, `app/security/rbac.py`, satisfied by
  `admin:*`; `read:*` does not grant it, the pattern of `admin:search_cadence`, `rbac.py:92-93`). The shell
  (`AppFrame`) asks `GET /api/v1/admin/history-backfill` once per session; a caller without the permission gets
  403 and sees nothing.
- **When:** after sign-in, at the first page, when at least one enabled console has **no decision**. It is a
  native `<dialog>` (the `Catalog`/`ExpandDialog` pattern, PRODUCT-UX-GUIDELINES §8): focus to the first
  control on open, Escape closes and returns focus. **Closing without choosing does not decide.** The dialog
  returns on the next sign-in; a one-line notice on `/admin` says "History backfill is not decided" until it is.
  A tab does not reopen it in the same browser session (a per-viewer convenience in `sessionStorage`, wrapped
  in try/catch).
- **What it states:**
  1. one question, as the heading: "Read the last 28 days from QRadar?";
  2. **the families** that will be read, each with its state ("Source volume", "WAF: 2 sources enabled, more
     read when you enable them", "IPS", "Catch-all senders", "Rule matches" only if enabled), and the families
     left out with the reason (Geography: "no baseline to fill");
  3. **the estimate** from `GET .../estimate`: searches, and "about N hours" at the current cadence, per
     family, from the static model of §B.8 and the first scan's density once known; a family whose window would
     be reduced says so ("WAF: 20 of 28 days fit in 72 hours");
  4. **Ariel's proved floor** once known, per family, from the scope rows (a first-run dialog has none yet; the
     admin page shows it later): "QRadar holds events from 2026-09-21 10:00 UTC; earlier hours are not claimed";
  5. **"Nothing old raises an alert."** and "Old data is used for learning only; it is never judged.";
  6. the cost in one sentence: "This sends read-only searches to QRadar for about N hours; the schedule you set
     in Search cadence still applies."
- **Choices:** **No** and **Yes**, the same visual weight, no default focus on Yes. Yes calls
  `PUT /api/v1/admin/history-backfill/{instance}` `{answer: "YES"}`; No `{answer: "NO"}`. After either, the
  dialog closes and the page it opened over shows nothing new; the decision is visible on the admin page.
- **States (all designed, tested):** loading ("Loading the history backfill status"), error (names what failed,
  that nothing was changed, and what to try; never the exception text), forbidden (the dialog is not rendered,
  not an error), already decided (not shown), saving (buttons disabled, `aria-busy`), conflict (a decision was
  recorded in the meantime: shows it).
- **Not asked:** which families, which month. The user decided (B2).

### B.10.2 The estimate must be honest

The estimate is a design-model figure (the table of §B.8), labelled "estimate", with the assumption stated
("5-minute buckets, 10 searches per tick, QRadar time per search not yet measured on your console"). **No ETA**
is promised; after reading starts, the page shows what was read, not a time of completion (as §H.9.1).

### B.10.3 The admin page

`/admin/history-backfill`, linked from the admin index (`frontend/src/app/admin/AdminIndex.tsx`), under
`PageHeader` (title "History backfill", description a fact: the window and the time zone). The question it
answers: **what has been read from QRadar's past, and what can still be?** First screen:

1. **Decision block** per console: the answer, who, when, how ("the installer" or the user), a **No** that can be
   turned to Yes ("Read history now"), and a Yes that can be stopped ("Stop reading").
2. **One table, one row per family and scope** (WAF and IPS list one row per source), columns: Family, Scope,
   State, Window asked (days), Window proved (days) and the **floor** (UTC), Read (days or buckets), Searches,
   Last step (UTC), Reason, **Later Yes** ("possible" / "not possible: this source already collected"). Numbers
   right-aligned in tabular figures; times with the zone; a relative age may follow, never replaces.
3. A progress cell reads "9 of 28 days" for `READING`; `PARTIAL` shows the reason in words and the gaps as time
   ranges; `UNAVAILABLE` is its own state with its reason, never an empty success; a family not read because it
   is off ("Rule matches: not enabled") is a row, not an absence.
4. Honesty states of PRODUCT-UX-GUIDELINES §4 and §6: `PARTIAL`, `TRUNCATED` (lower-bound share), `UNAVAILABLE`
   and `NOT_COLLECTED` map to their words.
5. **No chart** of history on this page. The charts of each family label history buckets in text ("read from
   QRadar history, not judged") with the neutral token already used for incident spans, plus the same text for
   assistive technology.

Permissions: read needs `read:history_backfill` (`read:*` grants it); changing needs `admin:history_backfill`.
Without read: the page says which permission is missing (the pattern of `search-cadence/page.tsx`). Every change
is audited (`history_backfill.decide`, `.start`, `.stop`) with the counts, never a value.

API (all additive; `app/api/routes/history_backfill.py`):

| Route | Permission | Does |
|---|---|---|
| `GET /admin/history-backfill` | read | decisions and scope rows per console |
| `GET /admin/history-backfill/estimate` | read | the model figures of §B.8 and the fitted windows |
| `PUT /admin/history-backfill/{instance_id}` | admin | record YES or NO; NO to YES allowed; write-once per direction, audited |
| `POST /admin/history-backfill/{instance_id}/start` | admin | "Read history now": creates the scope rows of every eligible family (§B.5) |
| `POST /admin/history-backfill/{instance_id}/stop` | admin | ends every `READING` scope `PARTIAL`, reason "stopped by <user>" |

None of them takes an AQL or a window from the browser; `days` is the stored setting.

## B.11 Relation to the property-watch bootstrap (PW-13)

**Yes: they should share one core.** The property-watch bootstrap (§H) and this design solve the same problem
in the same way, and four things in §H are already generic:

| Shared piece | Where it lives | In this design |
|---|---|---|
| the **planner**: floor scan, span and chunk sizing from observed density, halve-on-failure, gap list, density gate, completion test | `property_watch/bootstrap.py` (PW-13b, planned, pure) | `app/history/planner.py`, pure, used by both |
| the **`origin` convention** and its helper | `pw_bucket.origin` (§H.2.1) | `app/history/origin.py`: the column spelling for tables that may change, the `collection_source` spelling for Phase A, one `is_history_clause()` |
| the **floor proof** (derived from the read, margin, nothing before the floor) | §H.3.2 | §B.3.2, per scope kind |
| the **state machine and budget** (live first, sub-budget, seconds, max hours, fit the window) | §H.4.4, §H.5 | §B.6, §B.7 |

What stays different: property watches ask per watch (`history_days` on the watch, D-29), with a `NOT_REQUESTED`
state, a version-bump restart (§H.5.4) and the F.1 `include_history` flag; their tables are `property_watch`
columns, not `history_backfill_scope` rows. The platform answer does **not** govern them, and the dialog says so
in one line ("Property watches ask for history when you create the watch"). Their kill switch
(`pw_bootstrap_enabled`, default true by D-29) is unchanged.

### B.11.1 What changes in the PW-13 build order

PW-13a (foundations: migration `0029`, the `pw_*` enums and columns, the eight `pw_bootstrap_*` settings) is in
progress and **does not change**. Its `PwHistoryState`/`PwBucketOrigin` names stay; the shared core maps them.

| Was (§H.13) | Becomes |
|---|---|
| PW-13b: the pure planner for watches | **HB-1** builds the shared pure planner first, with property watches as its first client and its tests (the property tests of §H.13 PW-13b move there). PW-13b shrinks to a thin adapter (S, was M) |
| PW-13p: a lab probe for watches | **HB-9** is one probe for every scope kind (§B.3.5); PW-13p is folded into it |
| PW-13c1/c2/d/e/f/g | unchanged, but they call `app.history`. PW-13c2's reader classification guard (§H.2.2) becomes the shared guard test of §B.4.7 |

New order: PW-13a (running) in parallel with HB-1 -> PW-13b (adapter) and HB-2 -> PW-13c1, c2, d, e and HB-3..HB-8
in parallel -> HB-9 probe before any default is flipped -> HB-10 live validation. The only dependency HB adds to
PW is HB-1 before PW-13b. If the user prefers not to hold PW-13b, the fallback is to build PW-13b as designed and
refactor it into `app/history` when HB-1 starts: the cost is one rewrite of an already tested module (Q7).

## B.12 Fail closed, in one table

Every row is `INSUFFICIENT_DATA`, a gap, a named state with a reason, or no row. None is a zero, none is
`NORMAL`, none grants a detector lower-bound reasoning (repository rule 8); a detector's scope still comes from
`app/anomaly/capabilities.py`.

| # | Condition | Phase A | WAF | IPS | Catch-all | Rule matches |
|---|---|---|---|---|---|---|
| 1 | Events older than the floor | not written; `F_s` stored | not written; `F` stored | not written | not written | window absent: `missing` in coverage |
| 2 | No event in the whole window | `UNAVAILABLE`; no zero rows | `UNAVAILABLE`; source starts live | `UNAVAILABLE` | `UNAVAILABLE` | `UNAVAILABLE`; verdicts stay `INSUFFICIENT_DATA` |
| 3 | Retention shorter than window + reading (Phase A: Timescale policy) | window reduced to `retention - 3 d`, or refused with the reason | existing validator `config.py:882` | `ips_metric_retention_days` check | `catchall_retention_days` check | rule-match retention check |
| 4 | Property or field did not parse in old events | n/a (volume only) | `endpoint_coverage_ratio` stored; below `waf_min_endpoint_coverage` the bucket is `LOWER_BOUND`/`PARTIAL`, as live (`waf_endpoint_collector.py:786-801`) | `*_coverage_ratio` stored; below `ips_min_coverage` unknown, as live (`ips/inputs.py:276-283`) | n/a | n/a |
| 5 | Search truncated | split; one bucket is a lower bound | split; one bucket `LOWER_BOUND`, so `verify_endpoint_history` rejects it | split; cell `LOWER_BOUND` | split; bucket `LOWER_BOUND` | split; window `TRUNCATED` |
| 6 | Search failed or timed out | halve `W`; at the minimum width a recorded gap, `PARTIAL` | same; breakdowns `FAILED` per bucket as live | same, cells `FAILED` | same | `FAILED` window (as live) |
| 7 | Admission caps hit | n/a | `rejected_over_quota`/`admission_truncated` recorded exactly as live, so novelty fails closed for 7 days after | same | the same counters | n/a |
| 8 | Today's mapping applied to the past (A5) | n/a | current `profile_version` stamped; stated | current profile version; stated | current `catchall_type_ids`; stated | n/a |
| 9 | Time or budget ran out | `PARTIAL`, reason | `PARTIAL`; live starts at the boundary; the unread span is an implicit gap | same | same | same |
| 10 | Switch off, or stopped by an operator | `PARTIAL` "switched off" or "stopped by" | same | same | same | same |
| 11 | Source disabled or monitoring off during the reading | step skipped; state kept | `READING` held, resumes at `through` | same | n/a | n/a |
| 12 | Density gate | `PARTIAL` with the share | same | same | same | n/a |
| 13 | Console unreachable | step fails; nothing written; next tick retries | same | same | same | same |
| 14 | Estimate exceeds `max_hours` | window fitted to `D' >= 7`, else `UNAVAILABLE` | same | same | same | same |
| 15 | A decision is missing | nothing runs (undecided is not Yes) | nothing runs | nothing runs | nothing runs | nothing runs |
| 16 | A kind of F.1 rule reads a history row | LIVE-only loaders | LIVE-only | LIVE-only | n/a | n/a |

## B.13 The build

Sizes: S under half a day, M about a day, L two days. All wait for the user's review of this file, and the
backfill defaults stay as described until HB-9 has been read. Agent kinds: Codex for migrations and SQL-heavy cores, Sonnet for the rest.

| ID | Task | Agent | Size | Files | Depends on | Tests |
|---|---|---|---|---|---|---|
| **HB-1** | The shared pure core: planner (`scan` interpretation, span sizing, density adaptation, halving, gaps, density gate, completion, window fitting), the state machine and enums (`HistoryFamily`, `HistoryState`, `HistoryOrigin`), `origin` helper | Sonnet | M | new `backend/app/history/{planner,state,origin}.py` and unit tests | none | property tests: spans tile `[F, end)` exactly; a failed or truncated search never advances `through`; no row before `F`; the window fits or is refused; no I/O |
| **HB-2** | Foundations: migration `0030` (two tables, `origin` on four tables, enums), models, settings with validators, `HistoryFamily` registry, the `decide` CLI, RBAC permissions, audit | Codex | M | `alembic/versions/0030_history_backfill.py`, `app/models/history_backfill.py`, `models/{waf,ips,catchall,rule}.py`, `core/config.py`, `security/rbac.py`, `cli/history_backfill.py` | HB-1 (enums), PW-13a (migration `0029`) | migration round trip with data, `alembic check`, no server default left, CLI write-once on every path including the audit row and the missing-instance warning |
| **HB-3** | Phase A adapter and guards: scope planner (`WAITING` until a live bucket), history step in `collect_metrics`, `ariel_history` writer with `DO NOTHING`, per-source floor and zero-fill from `F_s`, the guards of §B.4.1 (both `evaluate_pending` branches, streak, F.1 loaders), baseline rebuild trigger, retention check | Codex | L | `collectors/metric_collector.py`, `anomaly/engine.py`, `custom_rules/loaders.py`, `workers/tasks.py`, new `history/phase_a.py` | HB-1, HB-2 | equivalence (live bucket by bucket vs history in chunks, equal except marker and provenance); history never pending (watermark NULL, after, predicate removed on purpose); first-run branch; run through `collect_metrics` on every exit path; statement count constant in sources and buckets |
| **HB-4** | Catch-all adapter: ledger-seeded history, per-source floor, forced `REVIEWED`, bootstrap hold while `READING`, `origin` | Codex | M | `collectors/catchall_collector.py`, new `history/catchall.py` | HB-1, HB-2 | equivalence; no alert from a history step even with alerts on; a live sender during the reading is `REVIEWED`; prepend with existing live senders; retirement at write |
| **HB-5** | Rule-match adapter: console-total floor, history windows `DO NOTHING`, replacement of the skipped-window `FAILED` records | Sonnet | M | `collectors/rule_match_collector.py`, new `history/rule_match.py` | HB-1, HB-2 | equivalence; coverage `COMPLETE` after a full read; a gap stays `missing`; `COMMON` stays a lower bound |
| **HB-6** | WAF adapter: bulk history persist (in-memory admission, per-bucket retirement, one statement per table), two passes, hand-off with the two watermarks, evaluation and F.1 guards, the `first_seen_at < history_end` rule | Codex | L | `collectors/waf_endpoint_collector.py`, `services/waf_pipeline.py`, `waf/engine.py`, `waf/detector.py`, `custom_rules/loaders.py`, new `history/waf.py` | HB-1, HB-2 | equivalence against the live persist with the same scripted provider; guards 1 to 3 with breaking each on purpose; statement-count pin (§B.8.4); novelty: history-time first appearance is `HEALTHY`/established; a flood in history blocks novelty for 7 days; resume after a worker restart |
| **HB-7** | IPS adapter: group-level history, hand-off, per-bucket retirement, guards, novelty rule | Codex | L | `collectors/ips_collector.py`, `services/ips_pipeline.py`, `ips/engine.py`, `custom_rules/loaders.py`, new `history/ips.py` | HB-1, HB-2 | as HB-6 for IPS |
| **HB-8** | API and service: the five routes, estimate, audit, permissions, eligibility per family (§B.5) | Sonnet | M | `api/routes/history_backfill.py`, `services/history_backfill.py`, `schemas/history_backfill.py` | HB-2 | permission matrix 401/403/404/409/422; write-once; NO to YES; start and stop; eligibility of a WAF source with live buckets |
| **HB-9** | **Lab probe** (read-only, §B.3.5): P-1 to P-9, at most 60 searches and 8 GETs; throwaway script outside the repository (the read-only rule applies: read only); a report with raw results and the verdict on A1 to A5 and the cost model | Sonnet | S | none in the repository; a report | none | the report states each **[C-n]** answered or not |
| **HB-10** | UI: first-run dialog, admin page, admin index link, states | Sonnet | L | `frontend/src/app/admin/history-backfill/**`, `frontend/src/components/history/*`, `frontend/src/components/auth/AppFrame.tsx`, `frontend/src/lib/api.ts` | HB-8 | Vitest for every state of §B.10; the colour-literal test; the three-viewport screenshots (the frontend quality bar) |
| **HB-11** | Installer: the question, the key, `check_value`, the non-interactive key, `HISTORY_BACKFILL` in the saved answers, step 9b, the summary line, docs | Sonnet | M | `qradar-dash`, `backend/tests/unit/test_qradar_dash_installer.py` and a new CLI test | HB-2 | the installer tests run `cmd_install` on every path: first install interactive yes and no, scripted yes, no, absent; a re-run with a different file never changes a decision; `--reconfigure` does not ask again; an upgrade never asks; a failing CLI warns and continues |
| **HB-12** | Live validation on the lab: Phase A, catch-all and a WAF/IPS source from the installer Yes path and the UI Yes path; the SQL check "no incident, breach, alert or transition with an interval before `history_end`"; hand-off with no gap and no overlap; a worker restart mid-reading; measured seconds per search | Sonnet | M | none; a report | HB-3..HB-11, HB-9 | the checks of §B.14.4 |
| **HB-13** | Docs: README and `INSTALL.md`, `CONFIGURATION.md`, `OPERATIONS.md`, `DATA-MODEL.md`, `API.md`, `SEARCH-CADENCE.md` §SC.6 | opencode free model | S | those files | all | none |

Order: HB-1 and PW-13a and HB-9 in parallel; then HB-2; then HB-3..HB-8 and HB-11 in parallel; then HB-10; then HB-12;
then HB-13. The longest chain is HB-1, HB-2, HB-6, HB-8, HB-10, HB-12.

## B.14 Open questions, console facts and disagreements

### B.14.1 Open questions for the user

**Q1. Is `HISTORY_BACKFILL` required in a scripted install?** Recommendation: **optional**. An absent key leaves
the console undecided and the UI asks; a required key would break every existing answers file and every
upgrade-by-reinstall.

**Q2. Should Yes cover WAF and IPS sources enabled after the answer, with no further question?**
Recommendation: **yes**, because it is the only way the order-dependent families can read history at all (they
cannot prepend). The dialog and the admin page say it.

**Q3. May a WAF or IPS source that already collected read history?** Recommendation: **no in this build**;
disable and recreate the profile. A prepend needs the value-ordering conflict resolved (§H.0.3) and is a
separate design.

**Q4. Breakdown history for WAF: 7 days?** Recommendation: **7**. Reading all three breakdowns for 28 days is the
dominant cost (§B.8.2: 78 + 31 + 31 of every 203 searches of a small source for 7 days; four times that for 28).
The cost of 7: ratio and concentration baselines have one visit per hour-of-week; they stay `INSUFFICIENT_DATA`
or rest on one visit until live fills the rest.

**Q5. The window fitting.** Recommendation: **fit to 72 hours and say so** (§B.6.5), rather than refuse a large
estate or read for a week. The user may prefer a longer limit (`history_backfill_max_hours`, up to 168).

**Q6. `NO_EVENTS` streak counts live rows only.** Recommendation: **live only**; the price is `silence_grace_buckets`
live buckets after the anchor (§B.4.1). The alternative lets a history silence open an outage alert at the first
live bucket.

**Q7. Hold PW-13b for HB-1?** Recommendation: **yes** (§B.11.1); the cost is a few days on PW-13b, the gain one
planner. The fallback refactors later.

**Q8. Geo and rule matches.** Recommendation: leave **Geo out** (no baseline); include rule matches **only when
enabled**. If the user wants the geography chart filled, it is a separate, later request with its own probe.

**Q9. Closing the dialog without choosing.** Recommendation: **it does not decide** and returns at the next
sign-in. The alternative (Escape means No) would silently remove the only entry for WAF and IPS.

### B.14.2 Console facts not established, each with its probe

| # | Fact | Probe | If otherwise |
|---|---|---|---|
| C-1 | How far back each scope kind holds events in the lab | P-1, P-2 | decides which lab demonstration runs |
| C-2 | Retention buckets and whether deletion is oldest-first per scope (A1) | P-3, P-1, P-2 | the run-time proof is not enough; the default stays No for that console |
| C-3 | Whether old events keep the payload a custom property reads | P-4 | coverage rules already fail closed; WAF/IPS history is `PARTIAL` or useless |
| C-4 | The ramp near the floor | P-7 | sets the margin |
| C-5 | Ariel time against width and rows for each family shape; whether 168 h stays under 120 s | P-5, P-6, P-8 | spans shrink and the tick counts of §B.8 grow |
| C-6 | Whether the `START/STOP` pad (`qradar_rest.py:293-314`) moves a count on windows days old | covered by P-5 | the pad is capped at "now" and floored at the end; a difference means a leak |
| C-7 | Whether `MIN(starttime) GROUP BY logsourceid` over 28 days is cheap enough to give Phase A a one-search floor | P-9 | the derived proof stands |
| C-8 | Whether the console-wide per-window total is a cheap proof for rule matches | P-1 (unscoped) | a longer first span |

### B.14.3 Where the code and the design text disagree (the code wins)

1. **Phase A retention.** The task text and the family docs speak of 35-day metric retention. For
   `log_source_metric` the code has none by default (`config.py:219-222`) and no validator ties it to
   `baseline_lookback_days`; WAF, IPS, catch-all, geo and PW have their own 35-day batch retention
   (`config.py:488,182,521,539,675`).
2. **Rule analytics' "30 days".** `rule_metric_lookback_days` (30, `config.py:274`) belongs to the offense-derived
   `rule_metric` collector, which reads our own database. The Ariel evidence is `rule_match`, judged over
   `rule_health_window_days` (30, `:298`), and is off by default (`:278`).
3. **First-run evaluation differs by family.** SEARCH-CADENCE §SC.6 says the first run evaluates only the
   latest bucket. Phase A does (`anomaly/engine.py:173-196`). WAF and IPS evaluate **every** bucket up to the
   collection watermark when the evaluation watermark is NULL (`waf_pipeline.py:273-280`,
   `ips_pipeline.py:258-266`); today only one bucket exists. History makes this a live hazard, hence guard 2.
4. **WAF collection is not constant per bucket.** PHASE-B §29.5 and `test_waf_scale.py` pin the statement count of a
   *run* (<= 40 for 2 000 endpoints); the run is 13 statements per bucket (§29.5 table) times at most 13
   buckets. Reading 8 352 buckets would multiply that (§B.8.4).
5. **Retirement uses the wall clock.** PHASE-B says `tracked_until` is the last collected bucket; the code does
   that for the bucket it is handed, but chooses *who* to retire by `now - waf_endpoint_active_days`
   (`waf_endpoint_collector.py:1138-1166`); IPS and catch-all retire by `now` and set no `tracked_until`
   (`ips_collector.py:586-599`, `catchall_collector.py:495-507`). A history chunk needs the bucket time.
6. **Catch-all "first run only bootstraps".** PHASE-C.2b says the first run bootstraps `REVIEWED`. The code keeps
   the bootstrap on until a run measured **every** bucket it searched (`catchall_collector.py:171-176`).
7. **Phase A zero-fill vs WAF.** The WAF module docstring explains why zero rows are not copied from Phase A;
   Phase A's fill is `monitoring_enabled` sources only (`metric_collector.py:447-456`), at the time of writing,
   not the time of the bucket. In history that is a fabricated zero for a source that did not yet exist (§B.3.2 rule 3).
8. **Catch-all row cap.** `catchall_max_rows_per_search` is 2 000 (`config.py:505`), not the 5 000 of
   `ariel_multi_bucket_max_rows`; `rule_match_max_rows` is 2 000 (`:296`) and capped at 5 000.
9. **The Phase A daily rebuild.** `rebuild_baselines` runs once a day (`celery_app.py:69-73`), so a history read
   without a rebuild trigger changes nothing for up to 24 hours.
10. **The F.1 engine seeds a watermark per subject kind only when the kind has none**
    (`custom_rules/engine.py:266-282`); a kind that has one reads every row after it, history included,
    unless the loader excludes it.

### B.14.4 Live validation (HB-12), the checks

- history rows carry the marker; contiguous 5-minute starts from the floor to the anchor, no duplicate; the first
  live bucket starts at the anchor;
- **none** of `log_source_anomaly`, `waf_anomaly`, `ips_anomaly`, `custom_rule_breach`, the alert table or their
  transition tables has an interval before `history_end` of its scope (SQL, asserted);
- the installer path: a Yes in the answers file produces the decision row before the first collection (the row's
  `decided_at` precedes the first `collection_watermark.last_run_at`); a re-run of the installer with `no` changes
  nothing; an upgrade does not ask;
- the UI path: a console with no decision shows the dialog to an admin and not to a read-only role;
- a worker restart between two history ticks resumes at `through` and the final rows equal an uninterrupted run;
- a WAF source with live buckets is `NOT_ELIGIBLE` and says why;
- measured seconds per history search by width and family, rows per span, and the statement count of a history
  tick for 1 and 10 sources.
