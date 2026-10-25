# Behavioral Analytics Architecture

Status: Phase A complete and live-validated (first shipped in `v0.4.0-phase-a`). Phases B to F and
the property watches of Phase G are **built**, each from its own design document and none from this
one: [PHASE-B-WAF-BEHAVIOR.md](PHASE-B-WAF-BEHAVIOR.md) (B),
[PHASE-C-LOG-SOURCE-FLEET.md](PHASE-C-LOG-SOURCE-FLEET.md) (C),
[PHASE-D-IPS-BEHAVIOR.md](PHASE-D-IPS-BEHAVIOR.md) (D),
[PHASE-E-RULE-ANALYTICS.md](PHASE-E-RULE-ANALYTICS.md) (E),
[PHASE-F-CUSTOM-DETECTIONS.md](PHASE-F-CUSTOM-DETECTIONS.md) (F) and
[PHASE-G-PROPERTY-WATCH.md](PHASE-G-PROPERTY-WATCH.md) (G). B to F first shipped in release
`v0.1.0`, the property watches and history bootstrap in `v0.1.2`. This document is the
architecture record: the later-phase sketches in §3.1 and §10.1 are superseded, and a phase is
still built only from its own design document, never from this one.

---

## 1. Primary product goal

This platform is a **QRadar behavioral anomaly and investigation platform**.

It learns what normal looks like from QRadar telemetry, detects deviations from
that normal, and answers one question:

> **What changed during the anomalous interval?**

The value is not the observation

> "EPS increased from 100 to 220."

but the explanation

> "EPS increased from an expected 95–110 to an observed 220. 68% of the increase
> is attributable to source IP 203.0.113.50, concentrated on destination port
> 445, with the DENY action rising from 21% to 84% of volume. 47 destination IPs
> were observed that do not appear in the baseline window."

Every anomaly the platform reports must therefore carry:

- expected value and observed value
- deviation and duration
- which event types, source IPs, destination IPs, ports, actions and usernames
  changed
- which values are new, and which disappeared
- which dimensions changed cardinality
- what evidence supports the claim, and how complete that evidence is

### 1.1 Relationship to the existing offense/rule/coverage functionality

The Phase 3 offense collection, rule inventory, rule health, detection coverage,
provider, collector and audit subsystems are **supporting infrastructure, not
the central product objective.**

They exist because behavioral analysis needs them:

- the **provider layer** is the only sanctioned, read-only path to QRadar and
  supplies the bounded Ariel aggregation the behavior engine depends on;
- **log-source inventory** defines the entities the engine baselines;
- **rule health and coverage** contextualize an anomaly (a volume drop on a
  source that feeds twelve rules is a detection-coverage incident, not just a
  telemetry curiosity);
- **alerting, notification and audit** are the delivery and accountability
  mechanisms for anomalies.

They must be preserved and kept working. They are not the product's headline.

---

## 2. Roadmap

| Phase | Scope | Status |
|---|---|---|
| **A** | Log-source volume behavior: EPS spike, drop, silence, seasonal baseline, anomaly-interval investigation, top-contributor explanation | **Complete**, live-validated 2026-08-02 |
| **B** | WAF behavior: endpoint request-rate baseline, error/block ratio, new endpoint access, peer-group baseline for new clients, request-pattern anomalies | **Built** — backend live-validated 2026-08-03; the ratio, concentration and novelty detectors with it (§30 of [PHASE-B-WAF-BEHAVIOR.md](PHASE-B-WAF-BEHAVIOR.md)); the analyst API and UI since 2026-10-13. Not built: peer groups for new clients (§27.9) and request-pattern anomalies (§11.3) |
| C | Firewall and IPS behavior: source IP × destination, source IP × destination port, distinct destination and port counts, deny-rate anomalies, scan behavior, exploit/signature bursts | **IPS part built** as Phase D ([PHASE-D-IPS-BEHAVIOR.md](PHASE-D-IPS-BEHAVIOR.md)) — per-source dimensions (source IP, port, protocol, signature), concentration, port fan-out and block ratio, live-validated 2026-10-16; not this sketch's pairwise keys. The rest is roadmap |
| D | User behavior: user × host, user × source IP, user × application, login-time behavior, new system access, failed-login patterns, privileged activity | Roadmap |
| E | Feedback and model management: false-positive feedback, threshold tuning, maintenance windows, baseline versions, model drift, detector performance | Roadmap |
| F | Internal LLM investigation assistant: evidence summarization, investigation plan, related historical anomalies, suggested AQL, internal playbook/RAG context | Roadmap |

**No LLM is permitted in Phase A.** The deterministic engine produces evidence;
a future phase may summarize it. Nothing in Phase A may call a model.

---

## 3. Common behavior-engine concepts

These concepts are shared by every phase. Phase A instantiates them for one
detector; Phases B–D reuse them without redefining the engine.

### 3.1 Entity

The thing whose behavior is being modeled. An entity is an ordered tuple of
identifying fields; the tuple is the natural key for baselines and anomalies.

- Phase A: `(qradar_instance_id, log_source_id)`
- Phase B: roadmap sketch was `(source_ip, normalized_endpoint, http_method)`;
  superseded by `(qradar_instance_id, waf_log_source_id, normalized_virtual_host,
  normalized_endpoint, http_method)` —
  see [PHASE-B-WAF-BEHAVIOR.md](PHASE-B-WAF-BEHAVIOR.md) §25.4
- Phase C: `(source_ip)`
- Phase D: `(username, destination_host)`

### 3.2 Metric

The scalar quantity observed per entity per time bucket.

- Phase A: `event_count`, and its derived `average_eps`
- Phase B: `request_count`
- Phase C: `distinct destination_port count`
- Phase D: `authentication_count`

### 3.3 Dimensions

Fields used to *explain* a metric change, not to define the entity. Dimensions
are only read during explanation, over a bounded window, and only in aggregate.

Phase A dimensions: `qid`, `event_name`, `source_ip`, `destination_ip`,
`destination_port`, `source_port`, `username`, `action`, `category`, `protocol`,
and magnitude/severity where meaningful.

### 3.4 Time bucket

A half-open UTC interval `[bucket_start, bucket_end)` on a fixed grid. Bucket
identity is deterministic: `floor(epoch / bucket_seconds) * bucket_seconds`, so
every worker agrees on which bucket an observation belongs to.

- Production Phase A bucket: **300 s**
- LAB_MODE Phase A bucket: **60 s**

All storage is UTC. Timezone conversion for display happens at presentation
time, using the user-selected timezone. The business-hours gate is a detection
decision: it converts the bucket start into the source's `timezone_name`
(see §4.3 and §5.3).

A bucket carries an explicit **completeness**: whether the collector believes it
observed the whole interval. An incomplete bucket may be displayed, but must
never enter a baseline and must never produce an anomaly verdict.

### 3.5 Baseline policy

How normal is computed. A baseline policy specifies a seasonality key, a
lookback, a minimum sample count, an estimator, and exclusion rules.

Phase A policy — see §4.

### 3.6 Detection policy

How a deviation becomes a verdict: the guards, their thresholds, and the number
of consecutive buckets required. A detection policy carries a **version**, and
every anomaly records the policy version that produced it, so a later reader can
tell whether a historical anomaly would still fire under current rules.

### 3.7 Explanation policy

Which dimensions to compare, over which windows, with what result bounds. The
explanation policy is deliberately bounded: it caps the number of dimensions,
the number of values returned per dimension, and the total query budget.

### 3.8 Anomaly lifecycle

See §6.

### 3.9 Evidence provenance

Every piece of evidence records where it came from: the exact AQL text (with no
credentials), the window it covered, when it was collected, how long it took,
and whether it was truncated. Evidence with no provenance is not evidence.

### 3.10 Completeness semantics

The platform distinguishes *"normal"* from *"we could not tell"*. Every layer
carries an explicit completeness or availability state, and an unknown is never
silently rendered as a healthy verdict.

- Buckets: `COMPLETE` / `PARTIAL` / `MISSING`
- Baselines: adequate samples, or `INSUFFICIENT_DATA`
- Anomaly state: `INSUFFICIENT_DATA` is a first-class state
- Evidence: `NOT_REQUESTED` / `PENDING` / `COMPLETE` / `PARTIAL` /
  `UNAVAILABLE` / `FAILED`
- Dimensions: a field absent from the DSM output is `UNAVAILABLE`, never zero

### 3.11 Limitations

Documented in §9.

---

## 4. Phase A baseline

### 4.1 Seasonal granularity

**Phase A seasonality is `weekday × hour`** — ISO weekday 1–7 crossed with hour
of day 0–23, giving 168 cells per (entity, metric). Samples in the same cell
across different weeks are treated as comparable.

This is the existing, working implementation and is retained deliberately. It
captures the two dominant SIEM seasonalities — the working day and the weekend —
without requiring months of history to populate.

A finer *bucket-slot-within-hour* model (e.g. 288 five-minute slots per day) may
be evaluated later. It is **not required for Phase A** and must not be
introduced as a rewrite of a working baseline.

### 4.2 Estimator

Median and MAD (median absolute deviation), not mean and standard deviation.
SIEM volume is heavy-tailed; a single incident spike would inflate a standard
deviation enough to blind the detector for weeks afterwards. Median/MAD is also
explainable to an analyst and reproducible from the stored samples.

Percentiles p05 and p95 are stored alongside as the displayed *expected range*.

### 4.3 Exclusions

A bucket is excluded from baseline computation when it is:

- inside a **maintenance window** for the source;
- an off-hours bucket for a **business-hours-only** source (expected-empty
  intervals must not be baselined as if they were real observations);
- inside the span of a **known anomaly**, resolved or not — an ongoing incident
  must not poison the baseline it will later be judged against;
- **incomplete** — the collector did not observe the whole interval;
- **unfinished** — the current, still-accumulating bucket is never baselined.

Off-hours are the source's `timezone_name` local time: a start later than the end
wraps past midnight and that window belongs to the local day it starts on, and an
unknown zone or an empty or out-of-range `business_days` fails closed so the bucket
is not excluded.

### 4.4 Adequacy and INSUFFICIENT_DATA

A baseline cell is *adequate* only when its sample count reaches the configured
minimum (`BASELINE_MIN_SAMPLES`; 8 production, 4 LAB_MODE). Below that the cell
is stored and displayed as "still learning" but **must not drive any verdict**.

When a detector is asked for a verdict without an adequate baseline it returns
**`INSUFFICIENT_DATA`**. It must never fall back to a misleading `NORMAL`.

### 4.5 Zero-MAD (degenerate scale) handling

MAD is exactly zero whenever more than half the samples are identical — very
common for a steady low-volume source. Two failures must both be avoided:

1. **Never divide by zero**, and never let a rock-steady source produce an
   infinite z-score on a one-event wobble.
2. **Never suppress a real, material deviation** merely because the robust score
   is uncomputable.

The engine therefore:

- floors the deviation scale at `max(|median| × BASELINE_MAD_FLOOR_RATIO, ε)`;
- records explicitly that the robust score was **degenerate** (MAD = 0) or
  **unavailable**;
- falls back to a deterministic expected-bound, ratio and absolute-delta test in
  that case, rather than declaring the source normal.

### 4.6 Baseline output

Each cell records: expected value (median), median, MAD, lower and upper
expected bounds (p05/p95), sample count, lookback start and end, baseline
version (monotonic, bumped per recomputation), baseline completeness, the
seasonality key `(weekday, hour)`, the last calculated time, and a bounded
sample of the observations used.

---

## 5. Phase A detection

Three detector types: **`VOLUME_SPIKE`**, **`VOLUME_DROP`**, **`NO_EVENTS`**.

A detector never fires on a bare percentage. Guards are conjunctive.

### 5.1 Spike logic

```
adequate baseline
AND complete metric bucket
AND observed volume above the source minimum
AND ratio guard satisfied            (observed / expected >= spike_ratio)
AND absolute-delta guard satisfied   (observed - expected >= min_absolute_delta)
AND (
      robust z-score >= threshold
      OR  robust score is degenerate (MAD = 0)
          AND the deterministic expected-bound fallback is satisfied
    )
AND required consecutive buckets satisfied
```

### 5.2 Drop logic

The inverse, with the same structure:

```
adequate baseline
AND complete metric bucket
AND expected volume above the source minimum
AND ratio guard satisfied            (observed / expected <= drop_ratio)
AND absolute-delta guard satisfied   (expected - observed >= min_absolute_delta)
AND (
      robust z-score <= -threshold
      OR  robust score is degenerate (MAD = 0)
          AND the deterministic expected-bound fallback is satisfied
    )
AND required consecutive buckets satisfied
```

### 5.3 Silence logic

`NO_EVENTS` considers expected activity, the last event timestamp, the source's
normal activity pattern for this seasonal cell, a configured grace period, and
collection completeness.

**A source that is normally inactive at that time must not raise `NO_EVENTS`.**
Off-hours silence on a business-hours-only source is healthy, not an anomaly.
Off-hours are the source's `timezone_name` local time: a start later than the end
wraps past midnight and that window belongs to the local day it starts on, and an
unknown zone or an empty or out-of-range `business_days` fails closed so the source
stays expected-active and silence can still raise `NO_EVENTS`.
An incomplete or failed collection is `INSUFFICIENT_DATA`, not silence.

### 5.4 Thresholds

All thresholds are configurable and resolved in order: global default →
per-criticality → per-source `custom_thresholds`.

**Production defaults are preserved.** The robust z-score threshold remains
**3.5**; the ratio and absolute-delta guards are *additional* conjunctive
conditions, so the change can only make the detector more conservative, never
noisier.

The absolute-delta and minimum-volume guards exist specifically to prevent the
classic false positive where 0.2 EPS becomes 0.4 EPS — a 2× ratio and a large
robust z-score on a change of no operational significance.

LAB_MODE may shorten the bucket interval, minimum sample count, confirmation
count, recovery count and collection schedule. **LAB_MODE must never silently
lower a production threshold.**

---

## 6. Anomaly lifecycle

```
                    ┌─────────────────────┐
                    │  INSUFFICIENT_DATA  │
                    └──────────┬──────────┘
                               │ baseline becomes adequate
                               ▼
                    ┌─────────────────────┐
              ┌────▶│       NORMAL        │◀────┐
              │     └──────────┬──────────┘     │
              │                │ 1 abnormal      │ N normal buckets
              │                ▼                 │
              │     ┌─────────────────────┐      │
              │     │      CANDIDATE      │──────┘
              │     └──────────┬──────────┘  returns to normal
              │                │ N consecutive abnormal buckets
              │                ▼
              │     ┌─────────────────────┐
              │     │        OPEN         │
              │     └──────────┬──────────┘
              │                │ normal bucket observed
              │                ▼
              │     ┌─────────────────────┐
              │     │     RECOVERING      │
              │     └──────────┬──────────┘
              │                │ M consecutive normal buckets
              │                ▼
              │     ┌─────────────────────┐
              └─────│      RESOLVED       │
                    └─────────────────────┘

     SUPPRESSED — reachable from any state by operator action or an
                  active maintenance window; preserves all evidence.
```

### 6.1 Required behavior

- One abnormal bucket may create `CANDIDATE`.
- The configured number of consecutive abnormal buckets promotes to `OPEN`.
- A normal bucket after `OPEN` moves to `RECOVERING`.
- The configured consecutive normal buckets move `RECOVERING` to `RESOLVED`.
- A `CANDIDATE` that returns to normal before promotion returns to `NORMAL`
  without ever having been an incident.
- **Duplicate `OPEN` anomalies must not be created for the same active
  incident.** One active incident per (entity, detector type).
- A recurring anomaly *after* resolution creates a **new** incident.
- Every transition is auditable: a persisted row with from-state, to-state,
  reason, the triggering bucket, and the timestamp.
- Manual suppression preserves historical evidence; it never deletes.
- **A collection failure must not resolve an anomaly.** Missing data is not
  recovery.
- **Incomplete data must not be interpreted as recovery.** Only a complete,
  genuinely-normal bucket advances the recovery counter.

### 6.2 Stored per anomaly

`opened_at`, `detected_at`, `anomaly_start`, `anomaly_end`, `resolved_at`,
detector type, severity, confidence, observed value, expected value, deviation
ratio, robust z-score, absolute delta, consecutive bucket count, baseline
version, detection policy version, evidence status, and the full lifecycle
history.

---

## 7. Explanation package

When a spike or drop reaches `OPEN`, the engine enqueues a **bounded**
explanation job. This is the most important Phase A capability.

### 7.1 Comparison

The job compares three things:

1. the **anomaly window**;
2. a **recent normal comparison window** (immediately preceding, complete,
   non-anomalous buckets);
3. **seasonal baseline evidence** where available.

It uses bounded aggregate Ariel queries only. It never runs an unbounded search,
and it never retrieves raw events.

### 7.2 Per dimension-value metrics

For each value of each available dimension, where defensible:

baseline count, anomaly count, absolute delta, percentage delta, share of
anomaly volume, contribution to the total increase or decrease, baseline rank,
anomaly rank, new-value flag, disappeared-value flag.

### 7.3 Per dimension metrics

Distinct-value count during baseline, distinct-value count during anomaly,
cardinality change, concentration change, top-contributor concentration,
new-value count, disappeared-value count.

### 7.4 Stored package

Anomaly ID, baseline window, anomaly window, comparison strategy, dimensions
analyzed, top contributors, newly observed values, disappearing values,
cardinality changes, evidence completeness, field availability, AQL provenance,
collection timestamps, sanitized errors, and a schema version.

Contributors are stored in a **typed table**, not an opaque JSON blob, so they
can be queried, ranked and aggregated. Small bounded payloads may use JSON only
where a typed column adds nothing and the shape is schema-validated.

### 7.5 What must never be stored

Raw QRadar events, full payloads, credentials, tokens, and request headers.
Phase A stores aggregate metrics and bounded aggregate evidence only.

---

## 8. Explanation completeness and honest language

### 8.1 Evidence states

| State | Meaning |
|---|---|
| `NOT_REQUESTED` | No explanation job was warranted (e.g. the anomaly never reached OPEN) |
| `PENDING` | Job enqueued or running |
| `COMPLETE` | Every requested dimension returned bounded, untruncated results |
| `PARTIAL` | Some dimensions succeeded; others were unavailable, truncated or failed |
| `UNAVAILABLE` | No requested dimension exists in this source's DSM output |
| `FAILED` | The job could not be completed; the sanitized reason is recorded |

If a field does not exist in the QRadar DSM output for a source, that dimension
is marked **`UNAVAILABLE`**. It is never rendered as a count of zero.

**Implemented (Phase A).** The investigation page renders each of these six
states with its operational meaning attached, and renders an `UNAVAILABLE` or
`FAILED` dimension as its own section rather than omitting it:

> Unavailable — this field was not exposed by the QRadar event schema or DSM for
> the selected interval.

Omission is the failure mode that matters here. Four of the six evidence states
produce a page with no contributors, which is visually indistinguishable from
"we looked and nothing stood out"; and a dimension section that simply is not
there reads to an analyst as one that *was* examined and came back clean. In
both cases the stated status is the only thing carrying the truth, so it is
always stated. A dimension's `new_value_count` and `disappeared_value_count`
default to 0 in storage but render as em dashes when nothing was counted.

### 8.2 The platform must not invent

Never fabricate usernames, actions, source IPs, ports, categories, event names,
or contributor percentages. A number that was not measured is not displayed.

### 8.3 The platform must not claim root cause

The engine reports *what changed*, not *why*. Required wording:

- "largest observed contributor"
- "largest volume change"
- "behavior is consistent with"
- "evidence suggests"

Forbidden wording: "caused by", "the root cause is", "this is an attack".

The deterministic engine produces evidence. **No LLM is permitted in Phase A.**

---

## 9. Limitations

- **QRadar exposes no per-log-source metric endpoint.** Volume is derived from
  bounded read-only Ariel aggregation over `events`. Ariel is subject to
  appliance load and retention.
- **Ariel must never execute through MCP.** The REST provider is the only
  sanctioned Ariel path.
- **DSM field coverage varies by source.** `username`, `action`, `category` and
  `protocol` are frequently absent. Dimension availability is per-source and
  discovered at explanation time, not assumed.
- **Late-arriving events** can change a bucket after it was collected. Buckets
  are re-collectable and idempotent, but a baseline computed before a late
  correction will lag until the next rebuild.
- **`weekday × hour` seasonality needs history.** A new source produces
  `INSUFFICIENT_DATA` until its cells reach the minimum sample count — by
  design.
- **Holidays and irregular schedules are not modeled** in Phase A. A public
  holiday looks like an anomalous weekday.
- **Retention.** Metric buckets and explanation packages are subject to a
  retention policy; historical investigation depth is bounded by it.
- **QRadar integration is strictly read-only.** No mutation endpoint exists, and
  none may be added.

---

## 10. Detector configuration

Phase A uses **typed Python configuration with strict validation**, not a
user-editable DSL. A dynamic DSL is explicitly out of scope: it would need a
parser, a sandbox and a migration story before it delivered any value the typed
form does not.

The Phase A detector, expressed conceptually:

```yaml
name: log_source_event_volume

entity:
  - qradar_instance_id
  - log_source_id

metric:
  type: event_count
  bucket: configurable            # 300s production, 60s LAB_MODE

baseline:
  strategy: seasonal_robust
  seasonality: hour_of_week       # ISO weekday x hour, 168 cells
  lookback: configurable          # BASELINE_LOOKBACK_DAYS, default 28
  minimum_samples: configurable   # BASELINE_MIN_SAMPLES, 8 prod / 4 lab

detection:
  spike_ratio: configurable
  drop_ratio: configurable
  robust_zscore_threshold: configurable   # preserved at 3.5 in production
  minimum_absolute_delta: configurable
  consecutive_buckets: configurable       # 2 prod / 1 lab

explanation:
  dimensions:
    - qid
    - event_name
    - source_ip
    - destination_ip
    - destination_port
    - username
    - action
    - category
    - protocol
```

### 10.1 Future extension points

The same engine must later express, **without modification to its core**:

```yaml
# Phase B — WAF (superseded sketch; see PHASE-B-WAF-BEHAVIOR.md §25.4)
entity: [qradar_instance_id, waf_log_source_id, normalized_virtual_host,
         normalized_endpoint, http_method]
metric: {type: request_count}

# Phase C — Firewall
entity: [source_ip]
metric: {type: distinct_count, field: destination_port}

# Phase D — User
entity: [username, destination_host]
metric: {type: authentication_count}
```

These are **not implemented**. They are listed to constrain the Phase A
abstraction boundary: anything in Phase A that cannot accommodate them is a
design defect.

The extension points that must stay generic are: entity definition, metric
definition, dimension set, bucket size, seasonality key, baseline strategy,
detector thresholds, explanation dimensions, model version, and policy version.

---

## 11. End-to-end flow

```
QRadar Ariel bounded aggregation   (read-only, REST provider only)
        │
        ▼
time-series metric buckets         (UTC grid, idempotent, completeness-tagged)
        │
        ▼
seasonal robust baseline           (weekday x hour, median/MAD, versioned)
        │
        ▼
spike / drop / silence detection   (conjunctive guards, degenerate-MAD fallback)
        │
        ▼
anomaly lifecycle                  (CANDIDATE -> OPEN -> RECOVERING -> RESOLVED)
        │
        ▼
bounded evidence collection        (aggregate Ariel, per dimension, capped)
        │
        ▼
contributor comparison             (anomaly vs recent normal vs baseline)
        │
        ▼
investigation API                  (authenticated, paginated, range-bounded)
        │
        ▼
investigation dashboard            ("What changed?")
```

**Phase A status:** every stage above is implemented, from metric buckets
through to the investigation dashboard at `/anomalies/[id]`, gated by automated
tests on both sides, and **exercised end to end against live QRadar telemetry on
2026-08-02** — spike, drop, multi-source isolation and `NO_EVENTS` each observed
through to `RESOLVED`. See [LAB-DEMO-RESULTS.md](LAB-DEMO-RESULTS.md).

**Phase B status:** the backend vertical slice — endpoint normalizer, collector, seasonal
baseline, `WAF_REQUEST_RATE_SPIKE` and lifecycle — is implemented and live-validated
(2026-08-03), and is off by default. Its anomaly API, UI rendering and Celery
schedule are **built** — the API and UI on 2026-10-13 (`app/api/routes/anomalies.py`,
`frontend/src/app/behavior/waf`) and the per-source beat schedule in
`app/workers/celery_app.py` (`dispatch_waf_collection`). The implementation contract is
[PHASE-B-WAF-BEHAVIOR.md](PHASE-B-WAF-BEHAVIOR.md), which supersedes the Phase B
entity and metric sketches in §3.1 and §10.1 above; §27 of that document is the
consolidated design for what remains. The roadmap's
`(source_ip, normalized_endpoint, http_method)` entity was assessed against live
data and rejected: client IP is unbounded and attacker-settable, so it is a
metric and a contributor dimension rather than an entity key.

---

## 12. Engine reuse boundary and detector capability

Audited against the working tree on 2026-09-13. This section records what the
code actually shares, what it deliberately does not, and which detectors can
actually run — so Phase C starts from facts rather than from §3's intent.

### 12.1 Shared primitives

Pure modules with no entity concept. Every behavior domain composes these.

| Module | Provides | Used by |
|---|---|---|
| `app/anomaly/statistics.py` | median, MAD, percentile, floored scale, `robust_z_with_status`, `ratio` | Phase A, Phase B |
| `app/anomaly/seasonal.py` | `seasonal_key`, `summarize_cell`, `retained_fraction`, `anomaly_exclusion_span`, `in_any_span`, `in_maintenance` | both baseline builders, both engines, WAF collector |
| `app/anomaly/lifecycle.py` | `next_state`, `advance_counters` | both engines |
| `app/anomaly/evidence.py` | `AnomalyEvidence[T]`, generic over the domain's detector-type enum | every detector |
| `app/models/enums.py` | `BucketCompleteness`, `AnomalyState`, `ACTIVE_ANOMALY_STATES`, `DetectorSignal`, `RobustScoreStatus`, `EvidenceStatus`, `DimensionAvailability` | every domain |
| `app/anomaly/capabilities.py` | detector requirement registry, `bucket_telemetry`, `detector_status` | Phase A engine, API |
| `app/anomaly/explanation.py` | contributor and dimension comparison over `DimensionAggregate` | Phase A; pure, reusable by Phase B evidence |

`seasonal.py`, `advance_counters` and the generic evidence type were extracted in
this audit from code that Phase A and Phase B had each written separately. The
extraction preserves behavior exactly, including exclusion-span precedence: a
resolved incident excludes through `resolved_at`, an unresolved one through
`anomaly_end`.

### 12.2 Domain bindings

Deliberately **not** shared. Each domain owns typed persistence and detectors.

| Concern | Log source (Phase A) | WAF endpoint (Phase B) |
|---|---|---|
| Entity | `LogSource` | `WafEndpoint` |
| Metric rows | `LogSourceMetric` | `WafEndpointMetric` |
| Baseline | `LogSourceBaseline`, `BaselineBuilder` | `WafEndpointBaseline`, `WafBaselineBuilder` |
| Detection input | `MetricPoint`, `DetectionContext`, `detectors.py` | `WafPoint`, `WafDetectionContext`, `waf/detector.py` |
| Thresholds | `ThresholdResolver` (criticality, `custom_thresholds`) | `WafThresholds` from `waf_*` settings |
| State, incident, transition | `LogSourceDetectorState`, `LogSourceAnomaly`, `AnomalyStateTransition` | `WafDetectorState`, `WafAnomaly`, `WafAnomalyTransition` |
| Engine | `AnomalyEngine`: alerts, maintenance, explanation hand-off | `WafAnomalyEngine`: maintenance (recorded per bucket) and alerting through the shared `AlertService` (`app/waf/engine.py`); explanation is the detector's reason text, not the Phase A package |
| Metric identity | `query_provenance` telemetry and aggregation; not enforced | `metric_semantics` on rows, buckets and cells; enforced fail-closed |
| Scheduling | `collect_metrics`, `evaluate_anomalies`, `rebuild_baselines` as separate jobs | one `collect_waf_source` run per source: collect → evaluate → train |
| Evidence package | `AnomalyExplanation*`, `ExplanationCollector` | not reused: a WAF incident serves `detection` from `waf_detection_detail` (`app/schemas/anomaly.py`) with its breakdowns and contributors |
| Detector-type enum | `AnomalyType` | `WafAnomalyType` |

**The rule for Phase C and later.** A new domain adds its own entity, metric,
baseline, state and incident tables, its own detector-type enum and its own
detector functions, and composes §12.1. It does not add columns to Phase A
tables, and it does not add members to `AnomalyType`, which is persisted in rows
and alert fingerprints. A generic `entity_key` table was rejected: it would move
typed, indexed, foreign-keyed Phase A columns into an opaque value and trade
every existing constraint for nominal genericity.

What is still duplicated between the two engines — incident creation,
transition persistence, worst-reading tracking — is orchestration over
domain-typed ORM rows. Extracting it would need a protocol over two unrelated
table families; it is not done until a third domain shows the shape.

### 12.3 Detector capability

A detector is only as real as its inputs. The live collection path is one
bounded Ariel aggregate per bucket, and it measures event volume and the newest
event time — nothing else. Every other metric column keeps a default, and a
detector reading `unknown_event_count = 0` reports a healthy ratio nobody
measured.

| Detector | Status | Why |
|---|---|---|
| `NO_EVENTS` | **Operational** | volume + `event_count` baseline |
| `VOLUME_SPIKE`, `VOLUME_DROP` | **Operational** | volume + `average_eps` baseline |
| `PARSING_DEGRADATION` | Synthetic only | Ariel aggregate computes no field-extraction ratios |
| `UNKNOWN_EVENT_SPIKE` | Synthetic only | Ariel aggregate counts no unclassified events |
| `TIMESTAMP_DELAY` | Synthetic only | the stored delay is newest-event-to-bucket-end, bounded by the bucket width; ingestion delay needs device time against receive time |
| `REPEATED_PAYLOAD` | Synthetic only | a payload signature requires reading payloads, which the platform never does |
| `CARDINALITY_DROP` | **Unsupported** | no `distinct_source_ip_count` baseline is built, and Ariel counts no distinct IPs |
| `COLLECTION_ERROR` | **Unsupported** | a failed collection raises before any bucket exists; failures live on the collection watermark |
| `WAF_REQUEST_RATE_SPIKE` | **Operational** (per enabled profile) | scheduled since Phase B.1 for sources with an enabled, validated profile; off by default; incidents served by the analyst API and UI since 2026-10-13 (`app/api/routes/anomalies.py`, `frontend/src/app/behavior/waf`). Declares `LOWER_BOUND_PROVES_INCREASE`: on a bucket whose counts are lower bounds it can prove a spike, never normality (Phase B §29.2) |

**Enforcement.** Each metric row's `query_provenance.telemetry` records what its
provider measured; rows collected before that key existed fall back to what
their `collection_source` is known to measure. `AnomalyEngine` runs a detector
on a bucket only when the bucket measured every input it reads **and** every
baseline it compares against is built. A detector that cannot run gets no
verdict, advances no counter and creates no state row; it is listed in
`EvaluationReport.unsupported` with the missing input. The same registry backs
`GET /api/v1/behavior/detectors`, so the published status cannot drift from what
executes.

**Existing state after upgrade.** Detector-state rows and incidents for the six
non-operational types are left untouched, neither deleted nor resolved. On live
data the only one that could ever have fired is `TIMESTAMP_DELAY`, on a source
whose `expected_interval_seconds` was set below a third of the bucket width. Any
such incident is no longer re-evaluated and needs an operator decision:

```sql
SELECT id, log_source_id, state, detected_at FROM log_source_anomaly
WHERE anomaly_type = 'TIMESTAMP_DELAY' AND state IN ('CANDIDATE', 'OPEN', 'RECOVERING');
```

### 12.4 Event counting

QRadar coalesces identical events into one Ariel record carrying an `eventcount`
multiplier. Every application AQL was audited:

| Query | Where | Volume | `COUNT(*)` |
|---|---|---|---|
| Log-source metric | `QRadarRestProvider.get_log_source_metrics` | `SUM(eventcount)` | none |
| Contributor dimension | `_aggregate_one_dimension` | `SUM(eventcount)`, ordered by it | none; `distinct_count` and `truncated` count returned rows because they measure cardinality |
| WAF grouped observation | `get_waf_observations` | `SUM(eventcount) AS request_count` | `record_count`, provenance only |
| WAF coverage total | `get_waf_observations` | `SUM(eventcount) AS total_events` | none |
| Scheduled searches | operator-authored | executed verbatim; the platform does not rewrite them | a search written with `COUNT(*)` counts records |

**Provenance.** Metric rows record `event_count_aggregation` and `telemetry`. WAF
rows record `request_count_aggregation`, the grouped `aql` and the `coverage_aql`
behind their completeness verdict. Rows collected before this audit carry
neither aggregation key; rows collected before `ae9932a` counted records.

**Mixed-history hazard.** Baselines look back 28 days. On a source that coalesces,
cells built partly from pre-`ae9932a` record counts expect less traffic than the
source sends, so a `VOLUME_SPIKE` can open on unchanged traffic until the old
rows leave the lookback. Metric rows do not record which query produced them, so
the platform cannot separate the two populations automatically. See
[PHASE-B-WAF-BEHAVIOR.md](PHASE-B-WAF-BEHAVIOR.md) §27.17 for the operator
procedure.
