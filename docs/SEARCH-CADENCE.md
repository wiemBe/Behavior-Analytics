# Search cadence — how often the platform searches QRadar (2026-10-12, amended 2026-10-18)

Status: **built and live-validated, first shipped in `v0.1.0`** (built 2026-10-12 on
`feat/phase-ce`, T-033 to T-039; the live validation T-040 ran the same day — §SC.10 is its
report). It rests on the live facts measured on the lab console on 2026-10-12: one search
returns every 5-minute bucket of its window (`starttime/300000` as the key), and an 8-hour Phase A
window of 184 rows takes 0.61 s.

## SC.0 What the user asked for

**The decision of 2026-10-18 replaces the 2026-10-12 default:** every search family now defaults to **5 min**,
except `WAF_COLLECTION` and `IPS_COLLECTION`, which default to **15 min** (amended 2026-10-18: at
5 min WAF costs 36–72 searches per source an hour). A new operator-defined search still defaults to
the 5 min schedule preset. The migration moves only the untouched 1 h seed rows
(`updated_by IS NULL`), to 15 min for WAF and IPS and to 5 min for every other family; an
operator-set cadence stays as set. Cadence
still changes only when QRadar is searched. Bucket widths, rule-match windows and every learning
period are unchanged. The 1 h statements below remain as the history of the 2026-10-12 decision and
are marked as replaced where they described a default.

The platform must not flood QRadar with Ariel searches. The user's requirements:
- Operators choose how often each search family runs: **5 min, 15 min, 30 min, 1 h, 2 h, 4 h or
  8 h**.
- **Historical default (2026-10-12), replaced on 2026-10-18:** 1 h for every family, Phase A included.
- Each family is set separately, from the UI, with no restart.
- Every change is audited.

The user accepted the cost: an outage (`NO_EVENTS`) alert can arrive up to one cadence late.

The search families are:

| Family | What it searches today | Searches today |
|---|---|---|
| `PHASE_A_VOLUME` | `SUM(eventcount)` per log source | 1 per 5-min bucket |
| `WAF_COLLECTION` | Endpoint groups, totals and breakdowns, per WAF source | About 3–6 per source per bucket |
| `ANOMALY_EXPLANATION` | Evidence for open anomalies | 2 per dimension per anomaly |
| `RULE_MATCH` | `GROUP BY creEventList` (T-025) | 1 per window |
| Scheduled searches | Operator AQL | Each search's own cron |

### Search count at the two defaults

Steady-state counts below come from the current dispatch and collection paths. They exclude
truncation splits and retries; the formula or configured cap is shown where a fleet shape changes
the count. `B` is the number of enabled WAF breakdowns (0–3), `P` is the IPS plan size after
grouping equal field mappings and chunking sources, `A` is the pending explanation count (0–5),
and `C` is a property watch's scope chunks (1–4).

| Family | Searches per tick at 5 min | Searches per hour at 5 min | Searches per tick at 1 h | Searches per hour at 1 h | Searches per hour at 15 min (WAF and IPS default) | Measured Ariel time (T-032/T-040) |
|---|---:|---:|---:|---:|---:|---|
| `PHASE_A_VOLUME` | 1 | 12 | 1 | 1 | n/a (5 min default) | 5 min controls: 0.04–0.10 s; 1 h keyed: 0.37 s, or 3.48 s on the first call of a process |
| `WAF_COLLECTION` | `2 + B` per source | `12 × (2 + B)` per source | `2 + B` per source | `2 + B` per source | `4 × (2 + B)` per source | 5 min controls: 0.06–0.17 s each; 1 h endpoints + totals: 0.14–0.15 s, source-IP breakdown: 0.08 s |
| `ANOMALY_EXPLANATION` | `20 × A` (default 10 dimensions, two windows) | at most 1,200 while a backlog remains | `20 × A` | at most 100 | n/a (5 min default) | Not measured in T-032/T-040. Each anomaly leaves `PENDING` after one attempt, so it is never searched again merely because another tick arrived |
| `RULE_MATCH` | 0 on eleven ticks, then 1 when the 1 h window closes | 1 | 1 | 1 | n/a (5 min default) | Default 1 h window: 1.11 s. A 1 h keyed run over twelve 5 min test windows took 1.17 s |
| `IPS_COLLECTION` | `P` (normally 6–7 for one mapping group; cap 16) | `12 × P` (cap 192) | `P` (cap 16) | `P` (cap 16) | `4 × P` (cap 64) | Not measured in T-032/T-040 |
| `PROPERTY_WATCH` | `C × (2 + pinned)` per watch; caps 12/watch and 40/instance | cap 480/instance | same formula; caps 12/watch and 40/instance | cap 40/instance | n/a (5 min default) | Not measured in T-032/T-040 |
| `CATCHALL_SENDERS` | 1 | 12 | 1 | 1 | n/a (5 min default) | Not measured in T-032/T-040 |
| `GEO_COLLECTION` | 2 with both default directions | 24 | 2 | 2 | n/a (5 min default) | Not measured in T-032/T-040 |

Every family resumes from its own watermark (or `collected_through`) and takes only complete,
settled buckets after it, so a tick that finds nothing new sends no search. A faster tick never
re-searches a collected bucket. Neither the first run nor `NOT_APPLICABLE` depends on the cadence (T-138): Phase A, WAF, IPS and the property watch read one interval (the latest closed
bucket) on their first run, the catch-all and geo first runs look back a fixed 12 buckets (1 h), and a property watch is marked `NOT_APPLICABLE`
after 12 consecutive collected buckets in which the property parsed on no event, counted across
ticks (`pw_collector.py`).

The Phase A, WAF and rule-match timings are lab measurements, not production capacity claims.
The T-040 lab was nearly idle and did not exercise a truncation split or a failing wide search.

## SC.1 The constraint: cadence is not the bucket width

The detectors are tuned for 5-minute buckets. Making the bucket 1 hour wide would break Phase A
outright.
- **Training:** each weekday×hour baseline cell would get one sample a week. That is at most 4 in
  the 28-day lookback, against `baseline_min_samples = 8`, so Phase A would never train.
- **Floors:** the 100-event absolute-delta floor is calibrated per 5-minute bucket.
- **Mixing widths:** the Phase A baseline builder groups by weekday×hour without filtering on
  `bucket_seconds`. A width change on a live install would put 5-minute and 1-hour counts in the
  same cell.

**So the cadence controls only when a family searches. The bucket width stays as it is.** At each
cadence tick, a family runs **one** search covering every complete bucket since its watermark. The
search is keyed by time, so it returns each bucket separately. Each bucket is then stored and
judged exactly as it is today.

At a 1-hour cadence this means 1 Phase A search an hour instead of 12. For WAF, 3–6 searches per
source per hour replace 36–72.

## SC.2 Facts (T-032, lab QRadar 7.6, 2026-10-12)

- **Bucket key.**
  - `starttime / <bucket_ms>` is accepted in `SELECT` and `GROUP BY`. It is integer division, so
    `k * bucket_ms` is the bucket start in epoch-UTC milliseconds, with no timezone involved.
  - `FLOOR(...)` is rejected.
  - `DATEFORMAT` is accepted, but its timezone is unproven and it would merge buckets at a DST
    change, so it is **not** used.
- **Equivalence.** The multi-bucket search and today's per-bucket query gave identical
  `SUM(eventcount)` and `MAX(starttime)` in 12 of 12 comparisons.
- **Cost.**
  - Phase A over 1 hour: 37 rows in 0.37 s.
  - Phase A over 8 hours (5 hours of data): 184 rows in 0.61 s, about 54 B per row.
  - A fleet of 51 sources at 96 buckets is about 4,900 rows.
- **`LIMIT` caps the whole result, not each bucket.** With `LIMIT 10`, 2 of 12 buckets lost
  every row. No per-bucket top-N exists in AQL.
- **`truncated` from `get_ariel_search_results` is always false.** The `Range` header caps the
  rows before the comparison runs. This is a live defect, fixed by T-033 (§SC.9).
- **`creEventList`** grouped with the bucket key is accepted: about 8–10 rows per bucket.
- **Settle.** Internal sources' last events sat 3–7 s before the window end. No forwarded source
  was live, so late arrival on real sources is unmeasured.

## SC.3 Data model (migration after T-025's 0012)

**`search_cadence`**: one row per family, seeded by the migration.

| Column | Type | Notes |
|---|---|---|
| `family` | `String(32)`, PK | A `SearchFamily` value. This is a new enum, not `AnomalyType`. |
| `cadence_seconds` | `Integer`, NOT NULL | `CHECK cadence_seconds IN (300, 900, 1800, 3600, 7200, 14400, 28800)`. A family's own migration seeds `3600`; migration `0028` moves an untouched seed row (`updated_by IS NULL`) to the 2026-10-18 default — `900` for WAF and IPS, `300` for every other family — and never touches an operator-set row. |
| `last_slot_start` | `DateTime(tz)`, NULL | Start of the last cadence slot this family was dispatched for. |
| `updated_at` | `DateTime(tz)`, NOT NULL | |
| `updated_by` | `String(255)`, NULL | The principal's subject; NULL for the seed. |
| `last_run_at`, `last_run_outcome`, `last_run_searches`, `last_run_buckets`, `last_run_splits` | NULL until the family first reports | Written by the family through `SearchCadenceService.record_run`. The API's `last_run` comes from these columns. NULL means "not run since the upgrade" and is never shown as a zero. |

Migration `0013` (T-034) creates `search_cadence`. Migration `0014` (T-035) creates
`anomaly_evaluation_watermark`.

**`anomaly_evaluation_watermark`**: one row per log source.

| Column | Type | Notes |
|---|---|---|
| `log_source_id` | FK, PK | References `log_source`. |
| `evaluated_through` | `DateTime(tz)`, NOT NULL | Bucket start of the last evaluated bucket. |
| `updated_at` | `DateTime(tz)`, NOT NULL | |

This is a new table, so Phase A tables stay as they are.

**The rest of the model:**
- The allowed cadences live in one place: a `SearchCadence` `IntEnum` in `app/models/enums.py`.
  The CHECK constraint, the API schema and the UI options all derive from it.
- `collection_interval_seconds` keeps its meaning, the bucket width. It **no longer** drives the
  beat schedule.
- The existing `*_interval_seconds` settings of the four families stop driving the beat.
  `explanation_collection_interval_seconds` and `rule_match_collection_interval_seconds` are
  replaced by the cadence row. Those settings are accepted and have no effect; see
  `docs/CONFIGURATION.md`.

## SC.4 Dispatch: the cadence gate

**The beat.** Each family's beat entry ticks every **60 s**. A tick first asks
`SearchCadenceService.claim_slot(family, now)`:

1. Compute the slot: `slot = floor_to(now - settle, cadence_seconds)`, aligned to epoch UTC. So
   4-hour slots start at 00:00, 04:00, 08:00 … UTC.
2. Claim it atomically:
   ```sql
   UPDATE search_cadence SET last_slot_start = :slot
   WHERE family = :f AND (last_slot_start IS NULL OR last_slot_start < :slot)
   RETURNING family
   ```
   - Exactly one tick wins a slot.
   - A losing or early tick returns `{"skipped": "not_due"}` and sends nothing to QRadar.
3. `settle` is a new setting, `search_cadence_settle_seconds`, defaulting to 120 s. A 1-hour slot
   is therefore dispatched at hh:02. T-032 gives only a lower bound for late arrival, so the
   value stays configurable.

**Changes and failures:**
- A cadence change takes effect from the next slot of the new cadence. No restart.
- Shortening the cadence never triggers a burst: a collector reads from its own watermark, so a
  tick collects only what is new.
- A family's run failing after it claimed its slot does not un-claim it. Collectors resume from
  their own watermark at the next slot, as today. Retrying inside a slot would multiply searches
  against a failing appliance.

**A family is gated in the same task that makes it multi-bucket. Never earlier.** The reason:
- Gated to 1 hour, a family still on per-bucket searches needs 12 or more buckets per run.
- Its per-run caps were sized for one bucket per run. Rule matches, for example, would fall
  behind the catch-up horizon and record windows as FAILED.
- Phase A would also evaluate only the latest of 12 new buckets.

T-034 therefore gates only `ANOMALY_EXPLANATION`, which has no buckets.

**What runs on the slot:**
- `PHASE_A_VOLUME` gates `collect_metrics`.
- `WAF_COLLECTION` gates `dispatch_waf_collection`.
- `ANOMALY_EXPLANATION` gates `collect_anomaly_explanations`.
- `RULE_MATCH` gates `collect_rule_matches`.
- `evaluate_anomalies` is DB-only and sends no search. It stays on a short beat of 60 s, and
  evaluates whatever new buckets exist (§SC.6).

## SC.5 The multi-bucket search contract (all collecting families)

1. **Key and bounds.**
   - Select `starttime / {bucket_ms} AS "k"` and group by the same expression.
   - Keep the half-open `WHERE starttime >= a AND starttime < b` **and** `START a STOP b`.
   - Here `a` is the first uncollected bucket start, and `b` is the current bucket boundary, the
     same rule as today.
2. **Window bound.**
   - At most `cadence_seconds / bucket_seconds + collection_max_backfill_intervals` buckets per
     tick.
   - The backfill cap now counts **beyond** one cadence. Today 12 buckets of backfill would not
     even cover a 2-hour cadence.
   - A history step is a deliberate exception to this bound: it reads the past, not the buckets
     the cadence is late for. It is bounded by its own budget (item 7) instead.
3. **Row budget and truncation.**
   - The AQL carries `LIMIT n + 1`, and the results read requests `n + 1` rows (after T-033).
     Here `n` is the new setting `ariel_multi_bucket_max_rows`, default 5,000, the largest value
     T-032 measured.
   - More than `n` rows means truncated.
   - **A truncated search keeps no bucket.** It is split into two halves of whole buckets, and each
     half is searched again.
   - The recursion ends at a single bucket. There the search is exactly today's per-bucket query,
     with today's truncation semantics.
   - The searches of one tick are capped by `search_cadence_max_searches_per_tick`, default 16.
     On reaching the cap, the collector stops. Its watermark stays at the last contiguous complete
     bucket, and the next slot resumes.
   - A bucket is never written from a truncated result.
4. **Fold.** A pure function maps rows to buckets.
   - It rejects any `k` outside the window. A malformed key fails the whole search.
   - **Equivalence is the acceptance bar:** for any window, the persisted rows per bucket equal,
     column for column, what per-bucket collection writes. This includes Phase A's zero-filled
     rows for silent monitored sources. Only provenance, `collected_at` and duration may differ.
5. **Provenance.** Each bucket's `query_provenance` adds:
   - `search_window_start` and `search_window_end`;
   - `search_bucket_count`;
   - `bucket_key` (the literal expression);
   - `split_depth`.
6. **Mock provider.** It implements the same multi-bucket methods, so unit tests and the lab
   profile exercise the real fold.
7. **The history step.** A family that was asked to read QRadar's past runs a history step inside
   its own task, after the live pass, under the same advisory lock. It is bounded by three
   budgets, never by the window bound of item 2:
   - **Searches.** The sub-budget is `min(history_backfill_searches_per_tick,
     search_cadence_max_searches_per_tick - searches the live pass already used)`. Live first is
     true by construction: a history step spends only what the live pass left, and a tick whose
     live pass used the whole cap runs no history search at all.
   - **Time.** No history search starts after `history_backfill_seconds_per_tick` seconds of the
     tick.
   - **Span.** One search never covers more than 168 hours, whatever the rows, and a search that
     returns more rows than the family's row cap is split as in item 3.
   The step resumes at its next tick from the stored progress, so the reading is spread over the
   ticks of the family's cadence rather than run in one burst. The rows it writes are marked as
   read from the past and are never judged (§SC.6, per family).

## SC.6 Per family

**`PHASE_A_VOLUME`**
- `MetricCollector` runs one keyed search per tick and folds it into per-bucket samples. It writes
  them through the existing upsert, and zero-fills per bucket.
- **Evaluation moves from "latest" to "every new bucket, in order".**
  - Today `evaluate_anomalies` calls `evaluate_latest`, so 11 of 12 buckets ingested in one tick
    would never be judged. The hysteresis counts (`anomaly_open_after_intervals`,
    `anomaly_resolve_after_intervals`) also assume one call per bucket.
  - The new loop evaluates buckets with `bucket_start > evaluated_through` in ascending order,
    each through the existing `evaluate_interval`. It then advances the watermark.
  - Each run is bounded by a new setting, `anomaly_max_buckets_per_evaluation`, default 120.
  - **First run** (no watermark row): evaluate only the latest bucket, as today, and seed the
    watermark. An upgrade must not replay history into alerts.

**`WAF_COLLECTION`**
- Per source per tick: one keyed endpoint search, one keyed totals search, and one keyed search
  per enabled breakdown.
- The **per-bucket top-N cut (`max_groups`) moves into the fold.** Each bucket keeps its top
  `max_groups` by request count. This is identical to today whenever the search was not
  truncated.
- Truncation splits as §SC.5. The single-bucket base case is today's query.
- The bucket's single Ariel permit becomes one permit per source per tick.
- WAF already evaluates per bucket through its own `evaluated_through` watermark. Verify it walks
  every new bucket in order, and pin that with a test at 12 buckets per tick.

**`IPS_COLLECTION`**
- Per source per tick, one keyed search per planned dimension over the source's own scope.
- The source's entities are the scope: destination ports, destinations, signatures, source
  concentration, port fan-out and action share.

**`CATCHALL_SENDERS`**
- Per console per tick, one keyed search over the configured device types, folded into
  per-source sender counts.

**`ANOMALY_EXPLANATION`**
- Only gated. The number of searches per anomaly is unchanged, and evidence arrives up to one
  cadence later.

**`RULE_MATCH`** (after T-025 merges)
- One keyed search per tick, `GROUP BY creEventList, starttime / {window_ms}`. Every window
  keeps its own `rule_match_window` row and completeness, as today.
- Counts are never summed across rule ids (T-032 Q6).
- Windows per run are `ceil(cadence / window) + rule_match_max_windows_per_run - 1`. When the
  cadence is no longer than the window, that is exactly today's per-run count.
- A failed multi-window search splits first and charges no window. Only a single-window failure
  counts an attempt. A down appliance costs at most `2n - 1` failing searches per tick.

**Asymmetry to settle in live validation.** On a failed (not truncated) multi-bucket search,
Phase A stops the tick and keeps the complete prefix, while rule matches split. If live runs
show wide searches timing out on a busy console, Phase A would hold its watermark and never
progress. In that case, give Phase A the same split-on-failure, bounded by
`search_cadence_max_searches_per_tick`.

**Scheduled searches**
- The editor offers the seven presets as cron. Under the 2026-10-18 default a new search defaults
  to 5 min; before it, 1 h. An existing search keeps its stored cron.

  | Preset | Cron |
  |---|---|
  | 5 min | `*/5 * * * *` |
  | 15 min | `*/15 * * * *` |
  | 30 min | `*/30 * * * *` |
  | 1 h | `0 * * * *` |
  | 2 h | `0 */2 * * *` |
  | 4 h | `0 */4 * * *` |
  | 8 h | `0 */8 * * *` |

- An existing cron that matches no preset shows as **Custom** and is kept verbatim.
- The API is unchanged; `schedule_cron` stays authoritative.

### The history step, per family

A family that was asked to read QRadar's past runs one history step per tick, after its live
pass, under the same lock. The step is bounded by the budgets of §SC.5 item 7. What differs per
family is the scope, the end of the window and what the step writes.

| Family | Scope | End of the window | What the step writes |
|---|---|---|---|
| `PHASE_A_VOLUME` | The whole console, one row per monitored source | The console's oldest live bucket (the anchor) | `log_source_metric` rows marked `collection_source = 'ariel_history'`, zero-filled from each source's own floor |
| `WAF_COLLECTION` | One WAF source | The moving boundary of the finishing tick | `waf_source_bucket` rows marked `origin = 'HISTORY'`, then the breakdown rows over `history_backfill_waf_breakdown_days` |
| `IPS_COLLECTION` | One IPS source | The moving boundary of the finishing tick | `ips_source_bucket` rows marked `origin = 'HISTORY'` |
| `CATCHALL_SENDERS` | The whole console, one row per catch-all source | The console's oldest live bucket | `catchall_bucket` rows marked `origin = 'HISTORY'`, and sender rows recorded as reviewed by the history |
| `RULE_MATCH` | The whole console, one window per rule | The console's oldest live bucket | `rule_match_window` rows marked `origin = 'HISTORY'` |
| `PROPERTY_WATCH` | One watch | The moving boundary of the finishing tick | `pw_bucket` rows marked `origin = 'HISTORY'` |

The end of the window matters: the console-wide families read the days before the anchor, so
their reading is complete in one direction; WAF, IPS and a property watch read towards a moving
boundary, so a long reading is cut short by the time limit.

A family whose scope is still waiting for its first live bucket runs no history search. A WAF or
IPS source that already collected live data never reads its past. A property watch reads its own
past only when it asked for it, with the sub-budget of the tick that is left after the enabled
watches' live cost.

Nothing a history step writes is judged. The rows carry the marker that says so, and every
detector's live-only readers exclude them.

## SC.7 API and permissions

**New permissions** in `app/security/rbac.py`:
- `read:search_cadence` — read-only roles get it.
- `admin:search_cadence` — admin only, the same pattern as `admin:waf`.

**`GET /admin/search-cadence`** returns one item per family:
- `family`;
- `cadence_seconds`;
- `allowed_cadences_seconds`;
- `bucket_seconds`;
- `last_slot_start` and `next_slot_start` (UTC);
- `updated_at` and `updated_by`;
- `last_run`, from that family's own collection report: `searches_sent`, `buckets_collected`,
  `split_count`, `outcome`.

`last_run` is null when the family has not run since the upgrade. It never shows a zero.

**`PUT /admin/search-cadence/{family}`** takes `{"cadence_seconds": <allowed>}`.
- Any other value gets a 422.
- An unknown family gets a 404.
- Writes an `audit_log` record holding the old and the new value.

## SC.8 UI

The page lives under `/admin`. The question it answers: **how often does each family search
QRadar, and what does that cost in latency?**

**Layout:** one table, one row per family, with these columns:
- the family name and a one-line scope;
- the cadence (a select of the seven presets);
- the bucket width;
- the worst-case detection delay, computed as `cadence + bucket + settle` and shown as text such
  as "up to 12 min" at the default 5-minute cadence (22 min for WAF and IPS);
- the last slot and the next slot (UTC);
- the searches sent in the last run.

**Interaction:**
- A change is confirmed inline and shows who changed it and when.
- The table is read-only for a principal without `admin:search_cadence`.

**Rules:**
- The frontend quality bar ([`PRODUCT-UX-GUIDELINES.md`](PRODUCT-UX-GUIDELINES.md)) applies in full.
- No KPI cards. Numbers right-aligned in tabular figures. Times in UTC.
- Designed states: loading, error, and "not run since upgrade".

**The scheduled-search editor** gets the preset picker (§SC.6).

**Truncated scheduled-search runs** (after T-033) also need UI work on `/searches/[id]`:
- The result count reads as a lower bound ("≥ 500"), not "500 (trunc)".
- The threshold column says "no verdict" for a truncated run that did not breach, instead of the
  "—" a run within its threshold also shows.
- The `total` trend now has no point for a truncated run. The chart must draw a gap there,
  never a zero (the frontend guidelines).

## SC.9 Tasks

| ID | Task | Depends on |
|---|---|---|
| T-033 | **Defect:** `get_ariel_search_results` can never report truncation, so a scheduled search capped at `max_result_rows` is recorded as complete. Reproduce first. Then fetch `max_rows + 1` and keep `max_rows`. Audit every caller. | — |
| T-034 | Cadence core: `SearchFamily`/`SearchCadence` enums, migration `0013` (seeded), `SearchCadenceService` (`claim_slot`, `record_run`, get/set), the gate helper, API and permissions, audit. Gates `ANOMALY_EXPLANATION` only. | T-025 merged |
| T-035 | Phase A: keyed multi-bucket collection, split on truncation, equivalence tests, evaluation watermark (migration `0014`), and Phase A's gate | T-033, T-034 |
| T-036 | WAF: keyed endpoint, totals and breakdown searches, per-bucket top-N in the fold, split, equivalence tests, and the WAF gate | T-033, T-034 |
| T-037 | Rule matches: keyed multi-window search, with per-run caps sized to the cadence, and the rule-match gate | T-025, T-034 |
| T-038 | UI: cadence admin page and the scheduled-search preset picker, with screenshots at three viewports | T-034 |
| T-039 | Docs: README configuration table, upgrade note, deprecated settings | T-034 |

**Live validation after the build:** run a 1-hour and an 8-hour cadence on the lab. Confirm one
Phase A search per tick, the per-bucket rows equal a per-bucket control run, and the alerts still
open on the generator's spike scenario.

## SC.10 Live validation (T-040, lab 7.6, 2026-10-12) — report

- **One search per kind per tick held.** Phase A at 1 h (12 buckets) and 8 h (96 buckets): one
  search each, no split. Rule matches at 1 h (12 five-minute windows) and 8 h (8 one-hour
  windows): one search each. WAF at 1 h: endpoints + totals, plus one per enabled breakdown.
  Nothing was truncated.
- **Equivalence.** Phase A matched the per-bucket path exactly in 6 of 6 buckets, zero-filled
  silent sources included. WAF matched in 3 of 3 data buckets (totals and all endpoint groups).
  Rule matches matched in 5 of 6 windows; the sixth differed by 5 events on 6 rules out of about
  2.4 million.
- **The `STOP` edge effect (low).** An event whose `starttime` is just before a span's `STOP` but
  that QRadar stored just after it is dropped by that search. The last bucket of a span is
  therefore slightly low, and the per-bucket path (every bucket is a last bucket) is the lower
  one. The keyed path is closer to the truth. Padding the Ariel `STOP` (not the `WHERE` bound) by
  the settle delay would make both exact; this is follow-up T-048, not a blocker.
- **Timing.** The 8 h searches took about 2.2 s; the slowest search was 3.5 s, the first of its
  process. No search failed or timed out. GROUP BY a quoted custom property with the bucket key
  works live.
- **The evaluation watermark (0014)** walked every new bucket in order for all 51 sources.
- **Not covered** (the lab is nearly idle: 282 rows in 8 h against a 5,000-row budget): truncation
  and the split path, a failing wide search (so the §SC.6 Phase A split-on-failure question stays
  open until a busy console is measured), the WAF action and response-code breakdowns, and a real
  Beat through `claim_slot`.
