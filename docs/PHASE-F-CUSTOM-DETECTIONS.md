# Phase F — custom detections and custom AQL (2026-10-15)

Status: **built, 2026-10-16** (designed 2026-10-15 by T-108). F-1 through F-7 and F-6a are built
and their sections are marked **[BUILT]**. An "As built" note under a section marks where the
code differs from the design text. It follows the shape of
[`PHASE-D-IPS-BEHAVIOR.md`](PHASE-D-IPS-BEHAVIOR.md) and rests
on [`SEARCH-CADENCE.md`](SEARCH-CADENCE.md) (the search cadence), [`PHASE-B-WAF-BEHAVIOR.md`](PHASE-B-WAF-BEHAVIOR.md)
§29 (measurement semantics, admission, bulk loaders), the dashboard's widget system and
[`PRODUCT-UX-GUIDELINES.md`](PRODUCT-UX-GUIDELINES.md). The design's
`file:line` citations were written against the pre-build tree; the "As built" notes cite the built
files.

---

## F.0 What the user asked (2026-10-15)

> "ayrıca şunuda ekleyelin custom aql yazma seçeneği buna bağlı grafik oluşturması etc. ve custom
> incident mantığı mesela bi kural yazcak bi log kaynağı şu eps i geçerse alert üret şu uri ya şu
> kadar istek gelirse alert üret gibi custom aql ile farklı bir şekilde mi yaparız sana bağlı"

Three wants: an option to write custom AQL; charts built from it; and custom incident logic —
user-written rules such as "alert when this log source exceeds this EPS" and "alert when this URI
gets this many requests". The "how" was decided on 2026-10-15: two tools on one alert pipeline
(§F.0a).

## F.0a The decision in one table

Two tools, one alert pipeline:

| | **F.1 Threshold rules** | **F.2 Custom AQL** |
|---|---|---|
| For | Conditions on data the platform **already collects** every bucket: a source's events/EPS, a class's EPS, a WAF endpoint's requests and ratios, IPS counts | Conditions the platform does **not** collect: a QID, a username, a custom property |
| Mechanism | A rule naming a subject, a metric, an operator, a threshold, a sustain and a recovery, evaluated in a DB-only tick | The existing scheduled searches: stored, validated, versioned AQL with a threshold alert |
| New Ariel searches | **Zero.** Rules read stored aggregates only | The search's own runs, under the existing Ariel budget and new caps (§F.8.2) |
| Fail-closed | As the detectors: unmeasured is `INSUFFICIENT_DATA`, never "below"; a `LOWER_BOUND` count proves "above" only | As today: a truncated run never resolves a threshold alert (`app/alerts/search_alerts.py:81-88`) |
| Alerts | `AlertService`, a new fingerprint kind, new enums — `AnomalyType` is not extended | The existing `search_threshold` and `search_failure` alerts (`app/alerts/search_alerts.py:6-14`) |
| What is missing | Everything: model, evaluator, API, UI | Only the UI and the charts: `/searches` is read-only today (`frontend/src/app/searches/page.tsx:6-65`) and no widget plots a search's results |

---

## F.1 Facts that constrain the design

### F.1.1 What is already collected, per subject and metric (question 1)

The platform measures these in **every bucket** (`COLLECTION_INTERVAL_SECONDS`, 300 s,
`app/core/config.py:223`). Each row below is a legal F.1 metric; nothing else is.

| # | Subject kind | Metric | Unit | Reads | Measurement |
|---|---|---|---|---|---|
| 1 | `LOG_SOURCE` | `event_count` | events / bucket | `log_source_metric.event_count` (`app/models/log_source.py:148`) | **EXACT** when the row exists and `completeness = COMPLETE` (`app/models/log_source.py:177-179`). No row ⇒ not measured. A `PARTIAL` row ⇒ no verdict either way |
| 2 | `LOG_SOURCE` | `average_eps` | events/s | `log_source_metric.average_eps` (`app/models/log_source.py:149`) | as #1 |
| 3 | `FLEET_CLASS` | `eps` | events/s | Derived per bucket: `SUM(event_count) / SUM(bucket_seconds)` over the class's monitored sources' `COMPLETE` rows, classed by `log_source_classification` with `UNCLASSIFIED` as the default (`app/fleet/summary.py:50-57,109-150`) | **EXACT** when every enabled, monitored source of the class has a `COMPLETE` row for the bucket; otherwise a **LOWER_BOUND** (missing sources can only add events) |
| 4 | `WAF_ENDPOINT` | `request_count` | requests / bucket | `waf_endpoint_metric.request_count` (`app/models/waf.py:453`; `SUM(eventcount)`, never `COUNT(*)`) | Per the bucket's `waf_source_bucket.request_count_measurement` (`app/models/waf.py:233-235`): `EXACT`, or **LOWER_BOUND** when grouping truncated or coverage fell below its floor |
| 5 | `WAF_ENDPOINT` | `block_ratio` | share ∈ [0,1] | `blocked_count / action_events` (`app/models/waf.py:460,467`) | `EXACT` when `action_measurement = EXACT` (`app/models/waf.py:248-253`) and `unmapped_action_count = 0` (`app/models/waf.py:470`); `NOT_COLLECTED` when the action breakdown did not run. A non-zero unmapped remainder makes the numerator a lower bound ⇒ proves "above" only |
| 6 | `WAF_ENDPOINT` | `server_error_ratio` | share ∈ [0,1] | `server_error_count / response_code_events` (`app/models/waf.py:474,472`) | as #5, through `response_code_measurement` (`app/models/waf.py:254-260`) and `unclassified_response_count` (`app/models/waf.py:477`) |
| 7 | `WAF_ENDPOINT` | `client_error_ratio` | share ∈ [0,1] | `client_error_count / response_code_events` (`app/models/waf.py:473,472`) | as #6 |
| 8 | `IPS_ENTITY` | `events` | events / bucket | `ips_entity_metric.events` (`app/models/ips.py:257`), sparse per admitted entity | Per the dimension's `*_measurement` on `ips_source_bucket` (`app/models/ips.py:158-166`): `EXACT`, or **LOWER_BOUND** when the dimension search truncated |
| 9 | `IPS_SOURCE` | `total_events` | events / bucket | `ips_source_bucket.total_events` (`app/models/ips.py:155`) | **EXACT** whenever the row exists (totals are complete or the row is absent) |
| 10 | `IPS_SOURCE` | `blocked_share` | share ∈ [0,1] | `blocked_events / (blocked + allowed + unmapped)` (`app/models/ips.py:181-183`) | `EXACT` when `action_measurement = EXACT` (`app/models/ips.py:171-173`) and `unmapped_events = 0`; a non-zero unmapped remainder makes the numerator a lower bound ⇒ proves "above" only |

**What a sustained breach needs in each case.** For an `EXACT` metric: N consecutive buckets, each
measured and each breaching. For a `LOWER_BOUND`-capable metric and an "above" rule (`GT`/`GE`):
a lower-bound bucket **counts toward the breach** (§F.5, rule 4) and **never counts toward
recovery**. For a "below" rule (`LT`/`LE`) on any metric: every counted bucket must be `EXACT`; a
`LOWER_BOUND` or missing bucket is `INSUFFICIENT_DATA`, exactly as the detectors treat a count
that bounds nothing (`docs/PHASE-B-WAF-BEHAVIOR.md` §29.2, lines 2881-2895).

Deliberately **not** in the catalog v1: client concentration on WAF endpoints
(`top1_client_requests` needs an exact breakdown, `app/models/waf.py:481-487`; Phase B keeps
client aggregates "possible, not built", §28.11), fan-out, novelty (both need verified history
windows), and every count the Ariel path does not measure — those are F.2's job.

### F.1.2 Measurement semantics are already a solved problem

The platform already distinguishes `EXACT` from `LOWER_BOUND` as a first-class concept
(`WafMeasurement`, `app/models/enums.py:373-392`) and already states, per detector, what a lower
bound may prove (`DetectorSpec.measurement`, `app/anomaly/capabilities.py:131-134`, with
`LOWER_BOUND_PROVES_INCREASE` at `app/anomaly/capabilities.py:246-247`). The WAF guarantee is
documented and property-tested: a stored count *c* satisfies *c ≤ T*, and every guard on the way
to a "spike" verdict is non-decreasing in *c*, so a lower bound that fires proves the breach —
while no verdict of "normal" is ever drawn from one (`docs/PHASE-B-WAF-BEHAVIOR.md` §29.2, lines
2869-2887). F.1 rules inherit this reasoning wholesale rather than reinventing it.

### F.1.3 Latency is the cadence, not seconds

Collection runs at each family's cadence (default **5 min**, and **15 min** for WAF and
IPS; `default_search_cadence`, `backend/app/models/enums.py:126-130`, `docs/SEARCH-CADENCE.md`
§SC.0) plus a settle delay (default 120 s, `docs/SEARCH-CADENCE.md` §SC.4). A detection can
therefore arrive up to *cadence + bucket + settle* late — about **12 min** at the 5-minute default
and **22 min** for WAF and IPS (`README.md` lines 96, 170). F.1 rules read the same buckets, so
their alerts inherit exactly this delay, plus at most one evaluation beat (60 s,
`app/workers/celery_app.py:42-46`). F.2 alerts arrive when the search's run completes: its cron
interval plus the run's duration (timeout defaults to 300 s, `app/models/search.py:66`).

**Neither tool is a real-time correlation engine.** QRadar's own rules are the tool for alerting
within seconds; this platform's detections are minutes-to-hours deep on collected aggregates. The
UI says so plainly, in the rule editor and on the rules page (§F.12).

### F.1.4 What scheduled searches already have (F.2's foundation)

Everything server-side exists and is tested:

- **Stored, validated, versioned queries.** `ScheduledSearch` holds the AQL and a version pointer
  (`app/models/search.py:53-54`); every AQL change mints an immutable `SearchQueryVersion` row
  with author and note (`app/models/search.py:90-110`; `app/services/scheduled_search.py:100-105`).
  Authoring-time validation refuses anything that is not a single bounded read-only SELECT —
  lexer, banned keywords, allowed datasets, mandatory time window, row limits
  (`app/services/aql_validator.py:32-45,126-199`), wired into the create and update schemas
  (`app/schemas/search.py:63-71,107-111`).
- **Threshold alerting that fails closed.** A completed run that crossed the threshold opens one
  alert (`search_threshold` fingerprint); a truncated run can open or keep a breach but **never
  resolves** one, because "not breached" is no verdict on a lower bound
  (`app/alerts/search_alerts.py:70-116`, truncation at 81-88; the executor records a truncated
  `threshold_breached` only for `GT`/`GE` from the proven bound `value + 1`,
  `app/services/search_executor.py:263-276`). A search that fails
  `search_failure_alert_after` (3) consecutive runs opens a `search_failure` alert
  (`app/alerts/search_alerts.py:118-159`, setting at `app/core/config.py:144`) — the
  quietly-stopped-detection blind spot.
- **Aggregated results, never raw events.** A successful run stores one `total` point (only when
  not truncated, `app/services/search_executor.py:222-233`) and one `SearchResultMetric` row per
  GROUP BY key with its `dimensions` (`app/services/search_executor.py:235-243`;
  `app/models/search.py:167-193`) — on a Timescale hypertable
  (`alembic/versions/0001_initial_schema.py:47`).
- **A write API behind permissions.** `POST /searches` and `PATCH /searches/{id}` require
  `search:write` (`app/api/routes/searches.py:73-85,96-120`); `POST /searches/{id}/run` requires
  `search:execute` and **executes the stored query only, never AQL from the request body**
  (`app/api/routes/searches.py:233-236`). Both permissions exist (`app/security/rbac.py:31-32`);
  today the admin role holds `search:write`, the analyst role only `search:execute`
  (`app/services/seed.py:21,30`). A result-trend read endpoint already exists with a
  `metric_key` filter and a most-recent-wins cap of 5,000 points
  (`app/api/routes/searches.py:139-223`).
- **Cost controls.** Per-search `timeout_seconds`, `max_time_range_hours`, `max_result_rows`
  (`app/models/search.py:66-68`) clamped by the platform settings (900 s / 168 h / 10,000 rows,
  `app/core/config.py:123-125`); cron scheduling with seed-don't-run, coalescing, overlap refusal
  and a bounded cycle (`app/services/search_scheduler.py:1-29`,
  `search_max_dispatch_per_cycle` = 10, `app/core/config.py:140`); executor concurrency of 3 per
  instance and 6 global (`app/core/config.py:121,131`).

### F.1.5 What is missing

1. **`/searches` is read-only in the UI** — a catalog table and a detail page that renders the AQL
   in a `CodePanel` marked read-only (`frontend/src/app/searches/page.tsx:6-65`,
   `frontend/src/app/searches/[id]/page.tsx:75`). No editor, no create, no enable/disable.
2. **No widget plots a search's results.** The initial widget catalog has no search widget.
3. **F.1 does not exist at all.**

---

## F.2 Scope

**In:** the F.1 rule model, evaluator, alerting, preview, API and UI over the ten metrics of
§F.1.1; the F.2 AQL editor UI and its validation feedback; the F.2 result-chart widget and the
chart's parity on `/searches/[id]`; caps and retention for both.

**Out:** free-text URI or pattern subjects (§F.3.3 explains why the WAF subject is a pinned
endpoint); rule templates or sharing between users; AI-assisted rule writing (Phase E is out of
scope, decided on 2026-10-15); alerting within seconds (QRadar's own rules); correlation across rules; any new
Ariel search for F.1; executing frontend-supplied AQL (never).

---

## F.3 F.1 — the rule model **[BUILT]**

> **As built** (`backend/app/models/custom_rule.py`, `backend/app/models/enums.py`, migration
> `backend/alembic/versions/0024_custom_rules.py`): the rule row, the subject-pairing `CHECK`,
> `uq_custom_rule_name` and the partial unique `uq_custom_rule_breach_active` are as designed.
> The status vocabulary is larger than this section's `last_status` sketch — see §F.5's note.

### F.3.1 The rule row

```text
custom_rule
  id UUID PK, created_at, updated_at
  name String(80) UNIQUE NOT NULL, description Text NULL
  subject_kind String(16) NOT NULL   -- LOG_SOURCE | FLEET_CLASS | WAF_ENDPOINT | IPS_ENTITY | IPS_SOURCE
  log_source_id  UUID FK log_source  NULL   -- set iff LOG_SOURCE or IPS_SOURCE
  source_class   String(32)          NULL   -- set iff FLEET_CLASS (SourceClass, app/models/enums.py:15)
  waf_endpoint_id UUID FK waf_endpoint NULL -- set iff WAF_ENDPOINT
  ips_entity_id  UUID FK ips_entity  NULL   -- set iff IPS_ENTITY
  metric String(32) NOT NULL         -- one of §F.1.1, validated against the catalog in code
  operator String(4) NOT NULL        -- GT | GE | LT | LE | EQ (the scheduled-search set,
                                     --   app/schemas/search.py:100)
  threshold_value Float NOT NULL     -- CHECK >= 0; ratios additionally in (0,1), validated in code
  sustain_buckets  Integer NOT NULL DEFAULT 1  CHECK >= 1
  recovery_buckets Integer NOT NULL DEFAULT 2  CHECK >= 1
  severity String(16) NOT NULL DEFAULT 'MEDIUM'   -- Severity, app/models/enums.py:92-98
  enabled Boolean NOT NULL DEFAULT false
  -- hysteresis and evaluation state (the Phase A pattern, app/models/log_source.py:293-296)
  consecutive_breaching Integer NOT NULL DEFAULT 0
  consecutive_under    Integer NOT NULL DEFAULT 0
  last_evaluated_bucket_start DateTime(tz) NULL
  active_breach_id UUID NULL
  last_evaluated_at DateTime(tz) NULL
  last_status String(16) NULL, last_status_reason Text NULL
  created_by String(255), updated_by String(255)
```

New enums in `app/models/enums.py` — `CustomRuleSubjectKind`, `CustomRuleMetric` — following the
pattern and the rationale of `WafAnomalyType`: `AnomalyType` is persisted in Phase A rows and
alert fingerprints, so a custom-rule value must never widen it (rule 6;
`app/models/enums.py:139-145` states the same reason for WAF). The subject-column pairing is
enforced by a `CHECK` in the migration, the pattern of `_IPS_SUBJECT_CHECK`
(`app/models/ips.py:353-358`); the metric is validated against the in-code catalog (§F.3.2) with
a guard test (§F.14), because the allowed metric set differs per subject kind.

### F.3.2 The metric catalog

A pure module `app/custom_rules/catalog.py`: one entry per §F.1.1 row, each stating its subject
kind, unit, the columns it reads, its measurement rule (`EXACT_ONLY` or
`LOWER_BOUND_PROVES_INCREASE` — reusing the requirement vocabulary of
`app/anomaly/capabilities.py:246-247`), and its bulk loader (§F.4.2). This is the F.1 analogue of
`DetectorSpec` (`app/anomaly/capabilities.py:119-147`): the rule's verdict scope comes from the
catalog, never from the evaluator's own reasoning.

### F.3.3 WAF and IPS subjects: admission and pinning (question 2)

**The decision: a rule never names a free-text URI. Its WAF subject is one admitted
`waf_endpoint` row, chosen at authoring; a rule on a URI that is not admitted is rejected at
authoring with the reason.**

Why, against the three options the task lists:

- *Reject the rule with a reason* — for a URI with no `waf_endpoint` row there is nothing to
  measure: endpoints below `waf_min_admission_requests` (3) per bucket or over the per-bucket
  quota (`waf_max_new_endpoints_per_bucket` = 20, `app/core/config.py:438,443`) are never stored
  (`waf_endpoint_metric` rows exist only for endpoints the grouped result mentioned,
  `app/models/waf.py:422-429`). Such a rule would be permanently `INSUFFICIENT_DATA` — fail-closed
  but useless, and a trap for the operator. The editor therefore offers only endpoints that
  exist, via the existing paged endpoint list `GET /behavior/waf/sources/{id}/endpoints`
  (`app/api/routes/anomalies.py:863-865`); a search that finds no endpoint explains *why*
  (admission is bounded per source and per bucket) and points at F.2 as the
  tool for conditions the platform does not collect. IPS entities pin the same way, by
  `ips_entity_id` (`app/models/ips.py:211-217`), from the existing entity inventory API.
- *Pin the endpoint inside the existing bounds* — pinning to the entity id (not to the natural
  key) is what keeps evaluation **constant SQL**: the bulk loader joins `waf_endpoint_metric` to
  `waf_source_bucket` for the union of rule endpoints in one statement (§F.4.2), never a query per
  endpoint. §29.11's "never a query inside the detector loop"
  (`docs/PHASE-B-WAF-BEHAVIOR.md` lines 3173-3185) applies verbatim.
- *Say "not collected"* — that is the **runtime** answer, not the authoring answer, for the two
  cases that can only appear after authoring:
  - the endpoint **retires** (`waf_endpoint.is_active = false`, `app/models/waf.py:366`): the
    rule's status becomes `SUBJECT_RETIRED`, it gets no verdict, no recovery, no resolution, and
    the rule page says the endpoint stopped being tracked and when;
  - the subject's **family is off** (WAF collection disabled, `waf_collection_enabled` default
    false, `app/core/config.py:398`; IPS the same, `app/core/config.py:159`): the rule's status is
    `NOT_COLLECTED` with that reason — the same vocabulary the detector-status API publishes
    (`DetectorStatus.NOT_ENABLED`, `app/anomaly/capabilities.py:66-77`).

A rule keeps a display snapshot of the subject (source name, endpoint's `display_name`
(`app/models/waf.py:405-408`), entity's dimension and value) in the breach's `details`, so the
alert stays readable after an entity is retired or deleted.

---

## F.4 F.1 — evaluation **[BUILT]**

> **As built** (`backend/app/custom_rules/engine.py`, `evaluate.py`, `loaders.py`,
> `backend/app/workers/celery_app.py`): the beat is `evaluate-custom-rules` every 60 s, a no-op
> that opens no session while `CUSTOM_RULES_ENABLED` is false. A kind is seeded only when it
> has an evaluable rule (enabled, not retired, family collected): that first run writes the
> watermark at the latest closed bucket and judges nothing. The metric read is one statement
> per such kind. The evaluation tick, and the non-opening and refresh alerting ticks, are
> pinned equal at 10 rules and at 100
> (`backend/tests/integration/test_custom_rules.py`,
> `backend/tests/integration/test_custom_rule_alerts.py`). A tick that opens alerts inserts
> one row per new alert; that cost is not in the pin. One state this section does not name: a
> kind whose grid is empty beyond `cadence + bucket + settle + 60 s` sets `last_status` to
> `STALE` on every evaluable rule of that kind. The breach row stays open, the counters and
> the watermark do not move, and the backlog is judged when rows return (F-1 review round 1,
> `evaluate.py::stale_bound_seconds`). A disabled, retired or family-off rule keeps its own
> status and does not take `STALE`. §F.4.4 adds the same terms up to "about 13 min" at the
> default 5-minute cadence (about 23 min for WAF and IPS at 15 minutes). Five minutes plus a
> 5-minute bucket, a 2-minute settle and at most one minute is 13 minutes.
> `/custom-rules` and `/custom-rules/new` show §F.12's sentence with "seconds-level" in place
> of "real-time" (`frontend/src/components/customrules/authoring.ts`), which still says "about an
> hour" and needs a code change. The README states the cadence, not that
> sentence (lines 96, 170).

### F.4.1 Where it runs

A new DB-only Celery task `evaluate_custom_rules`, on a 60 s beat like `evaluate_anomalies`
(`app/workers/celery_app.py:42-46`; that task is DB-only by design,
`docs/SEARCH-CADENCE.md` lines 145-147). It sends **no Ariel search, ever**. It is a no-op while
`CUSTOM_RULES_ENABLED` is false (default) — the WAF/IPS kill-switch pattern
(`app/core/config.py:398,159`) — and every rule then reports `NOT_ENABLED`.

Each subject kind has a watermark row (`custom_rule_watermark`, pattern of
`anomaly_evaluation_watermark`, `app/models/monitoring.py:58-71`): `evaluated_through` is the last
bucket start every rule of that kind was judged on. Evaluation walks buckets in ascending order —
the SC.6 rule that an hour-cadence tick must judge all twelve new buckets, not just the latest
(`docs/SEARCH-CADENCE.md` lines 193-199) — bounded by `custom_rules_max_buckets_per_run`
(default 288, one day of 5-minute buckets). **First run seeds the watermark at the latest complete
bucket and judges nothing**, so an upgrade never replays history into alerts (the Phase A
first-run rule, `docs/SEARCH-CADENCE.md` lines 199-201).

### F.4.2 Bulk evaluation and its cost (question 4)

Per tick, for each subject kind that has enabled rules:

1. **One statement** loads the kind's rules with their hysteresis state (one table; the columns
   live on the rule row).
2. **One statement** loads the metric window for the union of that kind's subjects since the
   kind's watermark — per kind:
   - `LOG_SOURCE`: `log_source_metric` rows for the named sources (`log_source_id IN (…)`),
     with `completeness` and `bucket_seconds` (`app/models/log_source.py:145,177-179`);
   - `FLEET_CLASS`: one `GROUP BY class, bucket_start` over `log_source_metric` joined to
     `log_source` and `log_source_classification` (`app/fleet/summary.py:55-57,109-150` is the
     shape), restricted to the classes rules name; the bucket is `EXACT` only when every
     enabled, monitored source of the class has a `COMPLETE` row, which the same statement
     counts;
   - `WAF_ENDPOINT`: `waf_endpoint_metric` joined to `waf_source_bucket` on
     `(log_source_id, bucket_start)` for the named endpoints, carrying
     `request_count`, the three ratio pairs and the bucket's measurement columns
     (`app/models/waf.py:453-487,233-265`);
   - `IPS_ENTITY`: `ips_entity_metric` joined to `ips_entity` and `ips_source_bucket`, with the
     dimension's `*_measurement` (`app/models/ips.py:244-258,158-166`);
   - `IPS_SOURCE`: `ips_source_bucket` rows for the named sources (`app/models/ips.py:155,171-183`).
3. The fold is pure and in-memory (sustain, recovery, honesty — §F.5). One flush writes rule
   state, breaches and watermark: **at most ~4 statements per subject kind per tick, ≤ 5 kinds,
   ≤ ~20 statements per tick — constant in the number of rules**, the same argument and the same
   regression-test pin as WAF §29.5 (`docs/PHASE-B-WAF-BEHAVIOR.md` lines 2994-3017).

The one accepted exception is the WAF/IPS one: opening an alert costs a few statements per *new*
alert through `AlertService.apply_bulk` (`app/alerts/service.py:158`), noted as such in
`docs/PHASE-D-IPS-BEHAVIOR.md` D.10 (lines 250-253).

**Caps (numbers).** `custom_rules_max_enabled` = **100** enabled rules; creating or enabling
beyond it returns `409` with the limit named (the dashboard-limits precedent). `custom_rules_max_buckets_per_run` = 288. At the
default 5 min cadences the steady-state tick judges one bucket per kind; before 2026-10-18, the default
1 h cadence made a tick judge 12 buckets per kind.

### F.4.3 Lifecycle: alerts and a breach record — not a fourth incident lifecycle (question 5)

**Decision: an F.1 breach reuses the alert lifecycle only, plus one lean breach row for the
observation trail. It does not join the incident lifecycle (`CANDIDATE → OPEN → RECOVERING →
RESOLVED`) and does not appear in the unified `/anomalies` list.**

What an analyst actually needs from a rule tripwire, and where it comes from:

| Need | Provided by |
|---|---|
| One alert per condition, no duplicates | `AlertService` + the partial unique index on `dedup_key` (`app/models/alert.py:46-51`) with a new `custom_rule_breach_fingerprint(rule_id)` (condition `custom_rule`, the pattern of `app/alerts/fingerprint.py:35-50,147-166`) |
| The values, honestly labelled | the breach row's `worst_value`/`last_value` with `worst_measurement`/`last_measurement` (`EXACT`/`LOWER_BOUND`) — the `observed_measurement` pattern (`app/models/waf.py:678-682`) |
| Recovery without flapping | the rule's `recovery_buckets`, the counter pattern of `app/models/log_source.py:293-296` |
| "When did it fire, what did it read" | the breach row's bucket bounds and the alert's `evidence_snapshot` (`app/models/alert.py:84-93`) |
| Investigation: *what changed* | the subject's existing pages — `/behavior/sources/[id]`, `/behavior/waf/sources/[id]`, the IPS views — which already carry baselines, bands and contributors. A threshold breach has no baseline, no explanation job and no contributor evidence to investigate |
| Tuning | the preview replay (§F.7) |

Why not the incident lifecycle: `CANDIDATE` exists to hold an unconfirmed *baseline deviation*
out of the incident list until hysteresis confirms it — but a rule's `sustain_buckets` **is** that
confirmation, so a CANDIDATE state would count the same delay twice. `RECOVERING` likewise
duplicates `recovery_buckets`. And the unified anomaly API projects Phase A, WAF and IPS
incidents (`app/api/routes/anomalies.py:142`); admitting operator tripwires there would blur "the
platform's detectors found this" with "my number crossed my line", which is exactly the
distinction an analyst triaging `/anomalies` relies on. The platform's own precedent for
"observation versus notification" is kept: an anomaly is an observation, an alert is the notified
lifecycle object (`app/models/log_source.py:310-312`) — the breach row is the observation.

`custom_rule_breach` (new): `rule_id` FK cascade, `state` checked to `OPEN | RECOVERING |
RESOLVED` (`AnomalyState` values reused, no enum change — the WAF/IPS precedent
`app/models/waf.py:614-616`), `first_/last_bucket_start`, `opened_at`, `recovering_at`,
`resolved_at`, the value/measurement pairs, `unmeasured_buckets`, a `subject_label` snapshot and
`details` JSONB. No transition table: the lifecycle is three states, the alert and its
notifications already record transitions (`app/models/alert.py:105-120`), and audit covers
configuration (§F.11). Deleting a rule resolves its open alert first (actor: the deleting
principal), then cascades.

### F.4.4 Latency, stated once more (question 3)

Worst case per family at the defaults: a **5-minute cadence + 5 min bucket + 2 min settle +
≤ 1 min evaluation = about 13 minutes** after the breaching bucket's events arrived; WAF and IPS
run at 15 minutes, about **23 minutes**. This is the platform's documented detection trade
(`README.md` lines 96, 170, `docs/SEARCH-CADENCE.md` §SC.8) and the UI states it in those terms.

---

## F.5 F.1 — fail-closed rules **[BUILT]**

> **As built** (`backend/app/custom_rules/evaluate.py`, `engine.py`): the statuses are
> `NOT_ENABLED`, `SUBJECT_RETIRED`, `DISABLED`, `NOT_COLLECTED`, `OPEN`, `RECOVERING`, `OK`,
> `NO_VERDICT` and `STALE`. Rule 7 names `NOT_ENABLED` for a disabled family; the code uses
> `NOT_COLLECTED` for that, with the reason `waf_collection_enabled is off` or
> `ips_collection_enabled is off`. A log-source or fleet-class rule is never
> `NOT_COLLECTED`. `NOT_ENABLED` is the fold's answer when the platform switch is off. The
> beat does not persist it: while `CUSTOM_RULES_ENABLED` is false it returns before opening a
> session, and the engine always folds with the platform on. `SUBJECT_RETIRED` covers a
> missing log source or IPS source (`the subject no longer exists`) as well as an inactive
> endpoint or entity; a fleet class is never retired. `OK` means no live breach and the last
> judged bucket was measured — a streak that has not reached `sustain_buckets` is still `OK`,
> not "under". `NO_VERDICT` means no live breach and the last judged bucket proved nothing
> (unmeasured or maintenance). `INSUFFICIENT_DATA` stays a bucket outcome. It increments
> `unmeasured_buckets` only while a breach is already open; it pauses a streak and does not
> reset it. Maintenance pauses the same way and is not counted as unmeasured. A rule never
> evaluated has a null status. What a `LOWER_BOUND` proves comes from the catalog: `event_count`,
> `average_eps` and `total_events` are `EXACT_ONLY`, so a lower bound proves nothing either
> way, including `GT`/`GE`. The other seven entries are `LOWER_BOUND_PROVES_INCREASE`.

1. **An unmeasured bucket is `INSUFFICIENT_DATA`.** No row for the subject and bucket (or, for
   WAF/IPS, no bucket record), a `PARTIAL` Phase A row, a `NOT_COLLECTED` breakdown: none of
   these ever reads as "under". It advances neither the breach counter nor the recovery counter,
   and it is counted in the breach's `unmeasured_buckets`.
2. **A `LOWER_BOUND` count proves "above" and nothing else.** For `GT`/`GE` it counts toward the
   breach (§F.1.2). For `LT`/`LE` it is `INSUFFICIENT_DATA` — a count that can only understate
   the truth bounds nothing below (`docs/PHASE-B-WAF-BEHAVIOR.md` §29.2, lines 2881-2895).
3. **Recovery needs `EXACT` "under" readings.** A breach resolves only after `recovery_buckets`
   consecutive measured-and-under buckets. Unprovable normality is not recovery — the WAF rule
   an `OPEN` incident keeps during a path flood (`docs/PHASE-B-WAF-BEHAVIOR.md` §29.3, line 2930).
4. **Ratios need their denominators exact.** While a denominator (`action_events`,
   `response_code_events`, the IPS action triple) is itself missing or a lower bound, the ratio
   has no verdict — a ratio over a lower bound bounds nothing (the `_ratio_inputs` rationale,
   `app/anomaly/capabilities.py:258-277`). An unmapped remainder (`unmapped_action_count`,
   `unclassified_response_count`, `unmapped_events` > 0) makes the numerator a lower bound: rule 2
   applies.
5. **The rule never grants itself scope.** Which metrics may conclude what from a `LOWER_BOUND`
   comes from the catalog entry (§F.3.2), the way a detector's verdict scope comes from its
   `DetectorSpec` (`app/anomaly/capabilities.py:131-134`).
6. **Evaluation is idempotent.** `last_evaluated_bucket_start` makes re-processing a bucket a
   no-op (the pattern of `app/models/log_source.py:296` and
   `app/models/waf.py:617-619`).
7. **A disabled family, a retired subject, a disabled rule: `NOT_ENABLED`, `SUBJECT_RETIRED`,
   `DISABLED` — statuses with reasons, never silence.** No verdict is drawn, and the rule page
   shows the state (the detector-status contract, `app/anomaly/capabilities.py:66-77`,
   published by `GET /behavior/detectors`, `app/api/routes/anomalies.py:595-604`).
8. **Nothing raw is persisted.** Rules read aggregates that already exist; breach rows store
   counts, shares and labels only.

---

## F.6 F.1 — alerting **[BUILT]**

> **As built** (`backend/app/custom_rules/alerts.py`, `backend/app/alerts/fingerprint.py`,
> `backend/app/custom_rules/engine.py`; the user's answer of 2026-10-16): shadow mode ships.
> `CUSTOM_RULES_ALERTING_ENABLED` (default false) gates paging while `CUSTOM_RULES_ENABLED`
> gates evaluation. With evaluation on and alerting off, breach rows and rule state are
> written and the evaluator opens, refreshes, resolves and enqueues nothing — including a
> rule disabled or a subject retired during shadow. The next alerting-on tick closes a page
> whose breach is already gone (`condition recovered`), resolves `DISABLED` and
> `SUBJECT_RETIRED` (the breach row is kept), and leaves `STALE`, `NOT_COLLECTED` and
> maintenance open. The alerting tick's orphan sweep resolves pages of rules deleted by
> cascade, actor `custom-rule-evaluator`. F-3's delete path resolves either way, alerting on or off,
> with the deleting principal. Maintenance is the source's live window plus the flag stored
> on the bucket; a fleet class has no window. The fingerprint prefix is
> `custom_rule_breach:`.

One alert per rule condition, through `AlertService`:

- **Fingerprint.** `custom_rule_breach_fingerprint(rule_id)` — `compute_fingerprint` with
  `source_type="custom_rule"`, `condition="custom_rule_breach"` (`app/alerts/fingerprint.py:35-50`).
  A new fingerprint kind, not a new member of any existing condition family, so it can never
  deduplicate against Phase A, WAF, IPS or search alerts.
- **Open/refresh/resolve.** A breach that opens (after `sustain_buckets`) calls `open_or_update`
  (`app/alerts/service.py:68`) with the rule's severity, `source_type="custom_rule"`,
  `source_id=rule_id`, `context` = rule name, subject kind, subject label, metric with unit, and
  `evidence` = observed value, measurement, threshold, operator, bucket bounds,
  `unmeasured_buckets`. A still-breaching `OPEN` breach refreshes it (the `occurrence_count`
  path, `app/models/alert.py:84-88`). Resolution (after `recovery_buckets`) calls
  `resolve_by_fingerprint` (`app/alerts/service.py:150`), actor `custom-rule-evaluator`, reason
  `condition recovered`. Notifications are enqueued per transition, exactly as the search
  evaluator does (`app/alerts/search_alerts.py:161-165`).
- **Switch.** `CUSTOM_RULES_ENABLED` (default **false**) gates the whole evaluator. Within it,
  each rule's `enabled` is its own switch. Both states are visible per rule (§F.5 rule 7).
- **Maintenance.** A source in maintenance does not open or refresh — the WAF/IPS rule
  (`docs/PHASE-D-IPS-BEHAVIOR.md` D.10, line 248) — and, following IPS, a maintenance bucket does
  not resolve either.

---

## F.7 F.1 — the rule preview (question 6) **[BUILT]**

> **As built** (`backend/app/custom_rules/preview.py`, `backend/app/api/routes/custom_rules.py`):
> both previews call `replay`, which runs the evaluator's own `fold_rule` over one bulk read —
> nothing is persisted, alerted or audited. The replay starts from a clean counter and ignores
> the operational halts (the rule disabled, the subject retired, the family or the platform
> off, `STALE`), so a draft or a disabled rule still answers the historical question;
> maintenance is applied exactly as the engine applies it. `hours` is 1–168, default 24.
> Agreement with evaluation is the hand-built cases in
> `backend/tests/integration/test_custom_rules_api.py`, not the property test the last
> bullet of this section names.

The preview answers *"would this rule have fired, on data that is already stored?"* It replays the
rule over collected buckets and sends nothing to QRadar.

- `GET /api/v1/custom-rules/{id}/preview?hours=24` (1–168): returns, per bucket in the window, the
  metric's `bucket_start`, `value`, `measurement` (`EXACT` | `LOWER_BOUND` | `NOT_MEASURED`) and
  the bucket's completeness, plus `breach_intervals` computed with the rule's exact sustain and
  recovery semantics, `fired_count` ("would have fired **N** times"), and the counts of measured
  and unmeasured buckets. The threshold and its unit are echoed for the chart.
- `POST /api/v1/custom-rules/preview` takes a **draft rule body** and returns the same shape.
  Nothing is persisted, nothing alerts. A draft preview is safe because the request is a
  structured selection over stored aggregates — it contains no AQL (rule 4 concerns AQL) — and the
  window is bounded and rate-limited like every other read.
- **Honesty is the chart's contract**: unmeasured buckets render as gaps (`connectNulls: false` —
  drawing a line across an uncollected interval turns a collector outage into an apparent
  recovery, `README.md` lines 530-535 and UX guidelines §4, lines 194-226); a `LOWER_BOUND` bucket
  that fires renders with its lower-bound marker ("at least n"); the threshold is one labelled
  line with its unit; breach intervals are shaded; the counts sit beside the chart, and an
  unmeasured majority is said in words, not implied by a sparse line (UX §4, lines 222-225).
- The same replay function drives evaluation and preview, so the preview cannot disagree with the
  evaluator — pinned by a property test (§F.14).

---

## F.8 F.2 — custom AQL: authoring, validation, preview **[BUILT]**

> **As built** (F-4: `backend/app/services/search_scheduler.py`,
> `backend/app/services/scheduled_search.py`,
> `backend/app/services/search_result_retention.py`, `backend/app/schemas/search.py`; F-5:
> `frontend/src/components/searches/SearchEditor.tsx`): the cron floor is
> `check_cron_floor` (300 s, a constant), computed by a fast UTC-mapped derivation with the
> fire-by-fire walk as the fallback, off the event loop. Authoring failures are
> `{"detail": {"message", "reasons"}}` for AQL and threshold refusals on create and update.
> A cron-floor refusal is 422 with a string detail, not that object. A patch whose only
> counted field is `enabled` (`change_note` does not count) is audited
> `search.enable`/`search.disable`. `search:write` is also on `detection_engineer`, not only
> on admin (§F.8.2's "admin only today" is the text from before 2026-10-16). The editor creates with
> `enabled: false`, keeps a non-preset cron verbatim as Custom, and previews with Run now on
> the stored draft. Its latency statement is the search-specific sentence ("This search runs
> in QRadar on its schedule; …"), not §F.12's custom-rules sentence (F-5 review round 1).

### F.8.1 The authoring flow

The browser never runs AQL. It submits text to be **validated and stored as a
version**; only stored versions run.

1. **Write.** `/searches/new` (and edit on `/searches/[id]`) offers the AQL text, the seven
   cron presets (5 min … 8 h, `docs/SEARCH-CADENCE.md` lines 235-243), the threshold with
   operator and severity, the guardrails, and `visualization_type` (§F.9.2's set).
2. **Save as a disabled version.** The editor saves with `enabled=false` (the create schema
   accepts it, `app/schemas/search.py:60`). Server-side validation runs at authoring time
   (`app/schemas/search.py:63-71`) and again before every run; the unreachable-threshold guard
   refuses a threshold the capped result could never breach (`app/schemas/search.py:73-85`).
3. **Preview = "Run now" on the stored draft.** `POST /searches/{id}/run` executes the stored
   query by id (`app/api/routes/searches.py:226-266`), never unsaved text; a manual run raises and
   clears the same alerts a scheduled run would (`app/api/routes/searches.py:254-260`). The
   preview costs one real bounded Ariel search and is rate-limited (search execution carries the
   tighter limiter, `README.md` line 206).
4. **Read the result.** The chart (§F.9) appears on the detail page, with the run's status and
   honesty states.
5. **Enable.** `PATCH` flips `enabled`; the version history (`/searches/{id}/versions`) and the
   audit trail show who changed what, when (`app/api/routes/searches.py:115-119`;
   `app/models/search.py:107-108`).

**Validation feedback.** `AQLValidationError.reasons` is a list — every problem at once
(`app/services/aql_validator.py:48-53`). Today the API folds it into one string
(`app/schemas/search.py:64-68`). Phase F returns the reasons as a list in the 422 detail (the
string kept for compatibility) and the editor renders them as an inline list under the editor,
each reason addressable, alongside the threshold-reachability reason. No reason is paraphrased
into something vague (the frontend guidelines ban "Something went wrong" copy).

### F.8.2 Cost guards (question 7)

| Guard | Value | Mechanism |
|---|---|---|
| Enabled custom searches | **25** (`scheduled_searches_max_enabled`, new) | Refuse at create/enable with `409` naming the limit (the dashboard-limits pattern) |
| Minimum cadence | **5 min** | The editor offers only the seven presets; a new or changed cron must fire at least 300 s apart, checked by computing successive fires with the scheduler's `CronTrigger` (`app/services/search_scheduler.py:9-12`). An existing cron that matches no preset is shown as **Custom** and kept verbatim (`docs/SEARCH-CADENCE.md` line 245) |
| Per-search window and rows | defaults 24 h / 10,000 rows | Existing columns (`app/models/search.py:67-68`), clamped by the platform caps 168 h / 10,000 (`app/core/config.py:124-125`) |
| Timeout | default 300 s, ≤ 900 s | Existing (`app/models/search.py:66`; `app/core/config.py:123`) |
| Ariel concurrency | unchanged: 3 per instance, 6 global | Existing executor limits (`app/core/config.py:121,131`); overlap refused and cycles bounded (`app/services/search_scheduler.py:20-23`, `app/core/config.py:140`) |
| Result retention | `search_result_metric_retention_days` = **90** (new) | A bounded daily batch task, the WAF/catch-all pattern (`app/core/config.py:477-479`). No retention exists for these rows today — the cap makes the gap matter (§F.13) |
| Who may write AQL | `search:write` — admin only today | Existing permission (`app/security/rbac.py:32`); admin role holds it, analyst does not (`app/services/seed.py:21,30`). "Run now" stays `search:execute` (`app/api/routes/searches.py:237`) |
| Audit of every version | already there | `search.create`/`search.update` with version before/after (`app/api/routes/searches.py:80-84,115-119`), immutable version rows with author and note (`app/models/search.py:107-108`). Phase F splits enable/disable into their own audited actions for readability |

**Worst case, stated.** 25 enabled searches at the 5-minute floor is at most 5 due per minute —
within the 10-per-cycle dispatch bound (`app/core/config.py:140`) — and QRadar never sees more
than 6 concurrent scheduled-search Ariel searches (`app/core/config.py:131`). Beyond the caps the
failure mode is *delayed runs, visible as late executions*, never unbounded load.

---

## F.9 F.2 — the result chart (question 8) **[BUILT]**

> **As built** (F-7: `backend/app/api/routes/searches.py`,
> `frontend/src/components/ResultTrend.tsx`, `frontend/src/widgets/SearchResultsWidget.tsx`):
> two reads beyond the trend endpoint feed the widget — `GET /searches/{id}/metric-keys`
> (distinct stored keys, `total` first) and `GET /searches/{id}/latest-groups` (the latest
> execution's grouped rows, with `values_are_lower_bounds` when it truncated). `ResultTrend`
> is the one component on `/searches/[id]` and in the widget, and a stored `PIE`/`HEATMAP`
> renders as the table with a note, as designed.

### F.9.1 Which result shapes chart

The executor stores two shapes (`app/services/search_executor.py:216-243`):

- **A time series per `metric_key`.** One `total` point per successful untruncated run
  (bucketed to the completed minute), and one point per GROUP BY key per run. A series charts as
  a **line over time** for a chosen `metric_key` — the existing trend endpoint serves exactly
  this, most-recent-first, capped at 5,000 points
  (`app/api/routes/searches.py:139-223`).
- **A top-N table.** The latest run's grouped rows (`metric_key`, `dimensions`, `value`,
  `app/models/search.py:181-193`), ordered by value, display-capped, each row a dimension key.

So `visualization_type` maps: `LINE` → the per-`metric_key` series; `TABLE` → the top-N table;
`SINGLE_VALUE` → the latest total; `BAR` → the series as bars. `PIE` and `HEATMAP` exist in the
enum (`app/models/enums.py:531-538`) but are **not offered** by the editor: a pie over an
unbounded GROUP BY would break the five-slice rule and no heatmap shape is
defined. An existing search stored with `PIE`/`HEATMAP` renders as `TABLE` with a note saying so —
rendered, never hidden.

### F.9.2 The widget and its states

A new configurable widget `chart.search_results` in the registry
(`frontend/src/widgets/registry.ts:40-93`): `requires: ["read:searches"]`,
`sources: ["/searches/{id}/results"]`, `config: { search_id, metric_key }` — both **enumerated**
options (the registry rule: pickers over ids, never free text reaching the server as a query).
It renders per the search's `visualization_type`
mapping above. Every `WidgetState` kind is designed and tested:

| State | Rendering |
|---|---|
| `ok` | the series/table with unit on the axis, tabular figures, UTC times |
| `empty` — never run | "This search has never run since it was created" + when it was created and how to run it now |
| `ok` + TRUNCATED qualifier | a truncated run stored no total (`app/services/search_executor.py:222-233`), so the point is **absent** — a gap in the line, never a zero (`docs/SEARCH-CADENCE.md` §SC.8, lines 296-302); the table shows "≥ n" for a truncated count and "no verdict" beside the threshold |
| `ok` + stale | the last successful run is older than twice the search's own schedule interval (derived from `last_run_at`/`next_run_at`; the widget system's stale rule) — say so, with the age |
| `error` — last run failed | the failure's `error_type` in words, the consecutive-failure count against `search_failure_alert_after` (3), and what to do: inspect the execution, edit the query, or wait for the retry |
| `forbidden` | names `read:searches` and says who can grant it |
| `loading` | the text-only placeholder |

The **same chart, same states, same component** on `/searches/[id]`: the existing `ResultTrend`
(`frontend/src/app/searches/[id]/page.tsx:120-127`, which already receives truncated-run times)
gains the `metric_key` selector and the top-N table view, so the widget and the page cannot drift
apart — one component, two mounts.

---

## F.10 Persistence and migration (question 9) **[BUILT]**

> **As built**: migration `0024` (`backend/alembic/versions/0024_custom_rules.py`) is the only
> Phase F migration — revision `0025` (`0025_auth_sessions`) is AUTH-1's, not Phase F. Breach
> retention (180 d) shipped in F-1, not F-2 as §F.15 splits it; the `search_result_metric`
> retention (90 d) is F-4's bounded daily task, and the optional Timescale chunk policy stays
> off (`retention_search_result_metric_days` is unset unless `RETENTION_ENABLED` is set).
> The breach delete also skips a `RESOLVED` row a rule still points at with
> `active_breach_id` (`backend/app/custom_rules/retention.py`). `0024` revises `0023`; the
> chain head in this release is `0025`.

One migration, `0024_custom_rules`, chained after the current head `0023_dashboards`
(`alembic/versions/0023_dashboards.py`). **Additive only**: three new tables —
`custom_rule` (§F.3.1), `custom_rule_breach` (§F.4.3), `custom_rule_watermark` (§F.4.1) — no
Phase A, WAF, IPS, catch-all, geo or dashboard table or enum is touched. F.2 needs **no new
tables**: `scheduled_search`, `search_query_version`, `search_execution` and
`search_result_metric` already hold everything (`app/models/search.py:29-195`).

Indexes: `uq_custom_rule_name`; `ix_custom_rule_subject (subject_kind, enabled)`;
`ix_custom_rule_breach_rule (rule_id, state)`; and a partial unique
`uq_custom_rule_breach_active` on `custom_rule_breach (rule_id)` where `state <> 'RESOLVED'` —
one live breach per rule, structurally, the exact shape of the alert dedup index
(`app/models/alert.py:46-51`). The breach table's FK cascades with the rule.
Downgrade drops the three tables, with their data, and restores `0023` exactly; the migration
must upgrade → `alembic check` → downgrade → upgrade → `alembic check` cleanly on a fresh
database, with a data-carrying round-trip test (the `0009` precedent,
`docs/PHASE-B-WAF-BEHAVIOR.md` §29.12, lines 3194-3212).

**Retention.** `custom_rule_breach_retention_days` = **180** (the incident-retention precedent
`waf_anomaly_retention_days` = 180, `app/core/config.py:476`), one bounded daily batch task under
`custom_rules_retention_batch_size` / `_max_batches` / `_interval_seconds` (the WAF pattern,
`app/core/config.py:477-479`); resolved breaches age out by `resolved_at`, live breaches never.
Rules themselves are kept until deleted. The new `search_result_metric` retention (90 days,
§F.8.2) rides the same task shape. Upgrade note for the README: nothing evaluates until
`CUSTOM_RULES_ENABLED` is set, so an upgrade changes no behaviour (the additive-migration
convention, `README.md` lines 683-699, needs no writer stop beyond it).

---

## F.11 Permissions, audit and API (question 10) **[BUILT]**

> **As built** (`backend/app/api/routes/custom_rules.py`, `backend/app/security/rbac.py`,
> `backend/app/services/seed.py`): `custom_rule` has no integer version — a patch carries
> `expected_updated_at`, the row's `updated_at` is the token, and a mismatch is 409 carrying
> `current_version` and `updated_by`. The enabled-count read is one unlocked `SELECT`, so two
> concurrent creates can both pass the cap. Validation returns every reason at once (422
> `{"detail": {"message", "reasons"}}`); the cap alone is 409 and the detail is that
> sentence. A duplicate name is also 409. A value outside an enum stays FastAPI's list 422.
> Audit actions are `custom_rule.create|update|delete|enable|disable` with before/after
> snapshots; enable and disable are used only when `enabled` is the only snapshot field that
> changed, and a rule created already enabled stays `custom_rule.create`. Previews are not
> audited. The `detection_engineer` role (the user's answer of 2026-10-16) is the analyst list plus
> `search:write` and `custom_rule:write`; `admin` keeps `admin:*`, which already satisfies the
> write, so the admin role does not list the string. `GET /custom-rules/catalog` is registered
> before `/{rule_id}` (`backend/app/api/routes/custom_rules.py`). The body is
> `CustomRuleCatalogOut` (`backend/app/schemas/custom_rule.py`): `metrics` (`metric`,
> `subject_kind`, `unit`, `is_ratio`, `measurement` as `EXACT_ONLY` or
> `LOWER_BOUND_PROVES_INCREASE`, `note` — not `reads` or `bulk_loader`), `subject_kinds`
> (`subject_kind` and the one `column`), `source_classes`, `operators`, `evaluation_enabled`,
> `alerting_mode` (`shadow` or `alerting`), `max_enabled`, `enabled_count`,
> `preview_hours_min` (1) and `preview_hours_max` (168).

**Permissions** (`app/security/rbac.py`, following the dashboard pattern at lines 104-106):

- `read:custom_rules` — satisfied by `read:*`; lists, reads, previews.
- `custom_rule:write` — create, update, delete, enable, disable. **Not** satisfied by `read:*`,
  like `dashboard:write` and `log_source:write`.
- The guard lives on the router mount, not on handlers, so adding an endpoint cannot ship it
  unauthenticated (`README.md` lines 483-485; the `/ips` precedent, `README.md` lines 993-995).
- F.2 permissions are unchanged: `read:searches`, `search:write`, `search:execute`
  (`app/security/rbac.py:31-32,77`).

**Audit.** Every rule write is an `AuditLog` row with action `custom_rule.create | .update |
.delete | .enable | .disable`, the actor, and before/after values (the outage-policy precedent,
`README.md` lines 923-927); `search.enable`/`search.disable` split out for F.2. Preview replays
are reads and are not audited, as for other list endpoints; "Run now" already is
(`app/api/routes/searches.py:261-265`).

**API** (prefix `/api/v1`, guards as above):

| Method and path | Purpose | Permission |
|---|---|---|
| `GET /custom-rules` | List with each rule's `last_status`, `last_status_reason`, `last_evaluated_at`, active breach, severity | `read:custom_rules` |
| `POST /custom-rules` | Create (validated: subject pairing, metric-in-catalog, ratio threshold ∈ (0,1), cap) | `custom_rule:write` |
| `GET /custom-rules/{id}` | One rule, with its hysteresis state and status | `read:custom_rules` |
| `PATCH /custom-rules/{id}` | Edit; carries the version it was made against (409 on conflict, the WAF-profile rule, `README.md` lines 502-503) | `custom_rule:write` |
| `DELETE /custom-rules/{id}` | Resolve-then-delete (204) | `custom_rule:write` |
| `POST /custom-rules/preview` | Draft replay (§F.7); nothing persisted | `read:custom_rules` |
| `GET /custom-rules/{id}/preview` | Stored-rule replay (§F.7) | `read:custom_rules` |
| `GET /custom-rules/{id}/breaches` | Bounded breach history with values and measurements | `read:custom_rules` |
| `GET /custom-rules/catalog` | Metric catalog, subject pairing and the editor's switches (`CustomRuleCatalogOut`) **[BUILT]** | `read:custom_rules` |
| `GET /searches` · `POST /searches` · `PATCH /searches/{id}` · `POST /searches/{id}/run` | Existing; the editor and the enable-cap use them | as today |

---

## F.12 UI (question 11) **[BUILT — the AQL editor (F-5) and `/custom-rules` (F-6)]**

> **As built**: the AQL editor (`frontend/src/app/searches/new/page.tsx` and the edit flow on
> `/searches/[id]`, `frontend/src/components/searches/SearchEditor.tsx`) creates disabled,
> keeps a Custom cron verbatim, lists 422 reasons inline, enables from the detail page, and
> says the search latency sentence of F-5 review round 1 rather than the custom-rules
> statement below. The custom-rules pages are `frontend/src/app/custom-rules/page.tsx`,
> `frontend/src/app/custom-rules/new/page.tsx` and
> `frontend/src/app/custom-rules/[id]/page.tsx`. The Detection entry is
> `frontend/src/lib/nav.ts`. Edit reuses the editor inside a disclosure on the rule page.
>
> The list's columns, which this section does not name, are Name, Status and reason, Subject,
> Condition, Severity, Enabled, Last evaluated (UTC). Status is second. Pills use words, not
> the tokens below: a null status is "Never evaluated", and `statusLabel` in
> `frontend/src/components/customrules/authoring.ts` maps `OPEN` to "Open", `RECOVERING` to
> "Recovering", `NO_VERDICT` to "No verdict", `STALE` to "Stale", `NOT_COLLECTED` to "Not
> collected", `SUBJECT_RETIRED` to "Subject retired", `DISABLED` to "Disabled" and
> `NOT_ENABLED` to "Not enabled". `OK` stays "OK". The engine's reason is shown beside the
> pill, unchanged. `INSUFFICIENT_DATA` is not a rule-status word; unmeasured buckets are gaps
> on the preview.
>
> This section's editor does not name an hours control. The draft preview
> (`frontend/src/components/customrules/CustomRuleEditor.tsx`) and the stored preview
> (`frontend/src/components/customrules/StoredRulePreview.tsx`) each take a Window in hours,
> from the catalog's `preview_hours_min` to `preview_hours_max`, starting at 24. Changing the
> window of a complete draft reloads that preview. The stored preview applies a new window
> only when the analyst presses Apply window; the rule page's first request is 24 hours.
>
> The sentence the list and the new-rule page show (`CUSTOM_RULE_LATENCY` in `authoring.ts`)
> uses "seconds-level" where the statement below says "real-time". The edit disclosure does
> not repeat it. `editorSummary` recovers with the complement of the operator ("at or below"
> after a greater-than) and names the catalog unit, not "EPS".

`docs/PRODUCT-UX-GUIDELINES.md` and `frontend/src/app/tokens.css` are normative; no new colour,
spacing or type literal; existing components only.

**Where things live.**

- **F.1 rules** — `/custom-rules`, `/custom-rules/new`, `/custom-rules/[id]`, in the
  **Detection** navigation group (which holds Offenses, Alerts, Rules, Rule statistics, Coverage —
  `frontend/src/lib/nav.ts:59-67`), labelled **Custom rules**. Rationale: these are detections an
  analyst triages through `/alerts`; the group is the detection-content surface, and the nav's
  rule is that an entry promises a working page (`frontend/src/lib/nav.ts:10-12`).
- **AQL editor** — `/searches/new` plus edit on `/searches/[id]`, under Operations' Searches
  (`frontend/src/lib/nav.ts:72`), because the catalog page is already the home of stored searches
  and its own copy says the frontend never runs ad-hoc AQL
  (`frontend/src/app/searches/page.tsx:19`).

**The rule editor** (`/custom-rules/new`, reusing the `fieldmapping` form patterns the WAF/IPS
editors established, `README.md` lines 492-505): a subject picker (kind, then the matching entity
picker: source search over `/behavior/sources`, class enum, WAF endpoint picker over
`/behavior/waf/sources/{id}/endpoints`, IPS entity picker), a metric picker enumerated from the
catalog **with its unit and its honesty note** (e.g. "requests per bucket — a truncated bucket
proves 'above' only"), operator + threshold with the unit label, sustain/recovery steppers with a
plain-language summary ("Fires after 2 consecutive buckets above 10 EPS; recovers after 2 buckets
below"), severity — and the live preview chart of §F.7 beside the form.

**The AQL editor**: the flow of §F.8.1, with the validator's reasons shown inline as a list, the
version diff in a `CodePanel` (code renders as text, never markup — UX §8, lines 406-410), the
preset picker (Custom kept verbatim), and the preview's result chart.

**The latency statement** appears once per editor and once on the list pages, in plain words:
"Rules are judged on collected 5-minute buckets, up to about an hour after the events arrive.
This is not a real-time correlation engine — for alerting within seconds, use QRadar's own
rules."

**States every view designs** (the frontend guidelines; a widget adds the stale state): loading; empty (no rules
yet — says what a rule is and that none exist; never-run search); error (what failed, what the
analyst can do); forbidden (`custom_rule:write` missing — the editor is hidden and the page says
who can grant it; the catalog is visible read-only); and the honesty states — `NOT_COLLECTED`
(WAF/IPS collection or the subject's family off, with the setting named), `SUBJECT_RETIRED`
(with the retirement date), `DISABLED`, `INSUFFICIENT_DATA` buckets in the preview (gaps plus the
count), `LOWER_BOUND` readings ("at least n"), TRUNCATED results (§F.9.2), stale. Numbers carry
their unit, use tabular figures and right-align in tables; times show UTC
(`docs/PRODUCT-UX-GUIDELINES.md` §7-§9). Vitest covers each state, and acceptance needs
screenshots at 1440×900, 1280×800 and 1024×768.

---

## F.13 Cost summary **[BUILT]**

> **As built** (`backend/app/core/config.py`, `backend/app/custom_rules/engine.py`): the caps
> and retentions exist as settings with the designed defaults (`scheduled_searches_max_enabled`
> 25, `custom_rules_max_enabled` 100, `search_result_metric_retention_days` 90,
> `custom_rule_breach_retention_days` 180). Ariel stays at zero new searches per custom-rule
> tick. The "≤ ~20 statements, constant in rule count" cell is the design's estimate: the pins
> show the evaluation tick and the non-opening and refresh alerting ticks are equal at 10 and
> at 100 rules, and a tick that opens alerts inserts one row per new alert (§F.4's note).
> Watermarks are one row per subject kind that has been seeded, and there are five kinds, not
> three. The latency cell repeats §F.4.4's "about 13 min" (about 23 min for WAF and IPS); the
> terms in that sentence add to 13 minutes at the 5-minute default.

| | F.1 | F.2 |
|---|---|---|
| Ariel searches | **0** per tick, by construction | The search's own runs; worst case 25 enabled × ≥5-min cron ≤ 5/min, ≤ 6 concurrent globally, ≤ 10 dispatched per cycle (§F.8.2) |
| DB per tick | ≤ ~20 statements at ≤ 100 rules, constant in rule count (§F.4.2) | Scheduler/executor as today |
| Preview | 1 bounded read of stored buckets | 1 real bounded Ariel run (rate-limited), 0 persisted |
| Storage | rules (rows ≤ 100) + breaches (≤ 1 live per rule, resolved aged at 180 d) + 3 watermark rows | `search_result_metric`, now retained at 90 d; ≤ ~1 + groups rows per run per search |
| Latency | cadence + bucket + settle + ≤ 60 s (≈ 13 min at the 5-minute default; ≈ 23 min for WAF and IPS) | cron interval + run duration |

---

## F.14 The tests that pin it **[BUILT]**

> **As built**: `backend/tests/unit/test_custom_rule_*.py`,
> `backend/tests/integration/test_custom_rule*.py` and
> `backend/tests/integration/test_migration_0024.py` (F-1 to F-3),
> `backend/tests/integration/test_search_guards_api.py`,
> `backend/tests/integration/test_search_result_retention.py`,
> `backend/tests/unit/test_search_result_retention_task.py`,
> `backend/tests/unit/test_cron_floor.py` (F-4),
> `backend/tests/integration/test_search_groups_api.py` (F-7), and the F-5/F-7 Vitest files
> under `frontend/src/app/searches/`, `frontend/src/components/` and
> `frontend/src/widgets/`. The custom-rules view tests are under
> `frontend/src/app/custom-rules/` and `frontend/src/components/customrules/` (F-6).
> One difference from the integration paragraph below: "preview equals evaluation" is pinned
> by hand-built agreement cases per metric family in
> `backend/tests/integration/test_custom_rules_api.py`, not by a property test over
> randomized bucket streams.

**Backend unit.** Catalog guard: every metric declares kind, unit, reader, measurement rule and
bulk loader; every kind has ≥ 1 metric; no metric reads a column outside §F.1.1. Validation:
subject-column pairing (each kind, each wrong pairing), metric not in catalog, ratio threshold
outside (0,1), operator set, sustain/recovery ≥ 1, the 100-rule cap. The evaluator fold, pinned
per guard of §F.5: N-sustain open; M-recovery resolve; **unmeasured counts neither way**;
LOWER_BOUND + GT opens and never resolves; LOWER_BOUND + LT is INSUFFICIENT_DATA; unmapped
remainder; retired subject; idempotent re-evaluation; first-run seeds without judging.

**Backend integration.** Migration `0024` round trip (upgrade → check → downgrade → upgrade →
check) with data carried; a statement-count pin at 10 and at 100 rules (equal counts — the
`test_waf_scale` pattern, `docs/PHASE-B-WAF-BEHAVIOR.md` §29.5, lines 3012-3017); alert
open → refresh → resolve with the new fingerprint, dedup pinned (two breaches, one alert);
nothing opens while `CUSTOM_RULES_ENABLED=false` or the source is in maintenance; preview equals
evaluation on identical stored buckets (property test over randomized bucket streams, including
gaps and lower bounds); the enable-cap and cron-floor refusals; 401/403/404/409/422 on every new
route; audit rows on every write.

**Frontend (Vitest).** Every state of §F.12 on each new view; the widget's UR.5.4 kinds and
qualifiers (§F.9.2); the preview chart's gap handling (`connectNulls: false`, no zero at an
unmeasured bucket), threshold line with unit, breach shading, "at least n" rendering; the AQL
editor's reason list from a 422; the latency statement present.

Every new test is checked by breaking the code on purpose.

---

## F.15 Task split (question 12) **[BUILT — F-1 through F-7 and F-6a]**

> **As built**: F-1 through F-7 and F-6a are in the code. Review changed the cron floor
> (§F.8) and replaced the search editor's latency sentence (§F.12). F-6 is the
> `/custom-rules` UI; F-6a is `GET /custom-rules/catalog`. F-8 wrote these markers while those
> two were still open; the placeholders are closed. Breach retention shipped in F-1, not under
> F-2 as the table below splits it (§F.10's note). The live lab validation remains the
> follow-up this section describes.

Phase F tasks are numbered F-1 to F-8 (F-1 to F-4 went to other work on 2026-10-15).

| ID | Task | Depends on |
|---|---|---|
| F-1 | F.1 core: enums, `custom_rule`/`custom_rule_breach`/`custom_rule_watermark`, migration `0024`, the metric catalog, the pure evaluator fold, `evaluate_custom_rules` beat task behind `CUSTOM_RULES_ENABLED`, per-rule status | Phase 5 on `main` |
| F-2 | F.1 alerting: `custom_rule_breach_fingerprint`, `AlertService` wiring, maintenance suppression, breach retention task | F-1 |
| F-3 | F.1 API: CRUD + preview replay + breaches, `read:custom_rules`/`custom_rule:write`, router guard, audit, caps | F-1 (preview needs the fold; alerts after F-2) |
| F-4 | F.2 backend guards: enabled-search cap (409), cron floor via `CronTrigger`, 422 `reasons[]`, `search.enable`/`disable` audit split, `search_result_metric` retention | Phase 5 on `main` |
| F-5 | F.2 UI: `/searches/new` + edit flow, preset picker, inline validation reasons, preview = Run now on the stored draft, enable; screenshots at three viewports | F-4 |
| F-6 | F.1 UI: `/custom-rules` list, editor with subject/metric pickers and live preview chart, rule detail with breaches; the latency statement; screenshots | F-3 |
| F-7 | `chart.search_results` widget (config `search_id`/`metric_key`), registry + catalog entry, `ResultTrend` metric_key selector and top-N table parity on `/searches/[id]` | F-5 |
| F-8 | Docs: README routes/data-model/settings, this file's [BUILT] markers | F-1–F-7 |

Order: F-1 → F-2 → F-3 → F-6; F-4 → F-5 → F-7; F-8 last. Live validation of F.1
on the lab (a generator-driven EPS spike firing a rule end to end) is a follow-up task to be
scheduled with the Phase D/F validation batch, not part of this split.

---

## F.16 Open questions for the user

**Answered by the user on 2026-10-16; these answers are binding and override the
text below where they differ:**
1. Shadow mode first: `CUSTOM_RULES_ALERTING_ENABLED`, default false, beside
   `CUSTOM_RULES_ENABLED`. Breaches are recorded; nothing pages until it is turned on.
2. The proposed caps and retentions stand as the setting defaults.
3. A new **detection-engineer** role between analyst and admin holds `custom_rule:write` and
   `search:write`, but none of the admin settings.
4. The F.2 preview is a real bounded Ariel run of the stored draft.
5. Breaches stay out of `/anomalies` (§F.4.3).

1. **F.1 alerting switch.** The design gates all evaluation behind `CUSTOM_RULES_ENABLED`
   (default false, the WAF/IPS pattern) and then alerts for every *enabled* rule — creating a
   rule is the opt-in. The alternative is the IPS-style shadow mode (`CUSTOM_RULES_ALERTING_ENABLED`
   default false: breaches recorded, nothing pages). Which do you want?
2. **Cap numbers.** 100 enabled rules and 25 enabled custom searches are the proposed ceilings;
   90 days for search results and 180 days for resolved breaches the proposed retentions.
   Confirm or adjust.
3. **Who writes rules.** `custom_rule:write` would go to the admin role only, matching
   `search:write` today. Should a detection-engineer role sit between analyst and admin?
4. **Preview cost for F.2.** The preview is a real bounded Ariel run of the stored draft. If that
   is too expensive on your console, the fallback is a validate-only preview (no run). The task
   text fixes the run-based preview; confirming.
5. **Custom-rule breaches in `/anomalies`.** The design keeps them out of the unified incident
   list (§F.4.3) — they surface in `/alerts` and on the rule's page. Say so if you want them in
   `/anomalies` too.
